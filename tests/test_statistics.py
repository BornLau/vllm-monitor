import json
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from test_app import app, project


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        app.completed_usage_query.cache_clear()

    NOW = 1789984800

    def fixture(self, url, **kwargs):
        params = parse_qs(urlsplit(url).query)
        expr = params['query'][0]
        value = 2 if 'num_requests_waiting' in expr else 3 if 'request_success_total' in expr else 10 if 'prompt_tokens_total' in expr else 20
        if 'num_requests_waiting' in expr:
            self.assertIn('avg_over_time(', expr)
        else:
            self.assertIn('increase(', expr)
        labels = {'monitor_project':'one','monitor_node':'engine','model_name':'model-a'}
        if 'query_range?' in url:
            self.assertIn('[1d]', expr)
            self.assertEqual(params['step'], ['86400'])
            points = [[t,str(value)] for t in range(int(params['start'][0]),int(params['end'][0])+1,86400)]
            result = [{'metric':labels,'values':points}]
        else:
            result = [{'metric':labels,'value':[int(params['time'][0]),str(value)]}]
        return json.dumps({'status':'success','data':{'result':result}}).encode()

    def test_daily_buckets_totals_and_queue(self):
        with patch.object(app.time,'time',return_value=self.NOW), patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',side_effect=self.fixture):
            data=app.usage_statistics('7d')
        self.assertEqual(len(data['dates']),7)
        self.assertEqual(data['totals']['requests'],21)
        self.assertEqual(data['totals']['total_tokens'],210)
        self.assertTrue(all(day['waiting']==2 for day in data['rows'][0]['daily']))
        self.assertFalse(data['partial'])
        self.assertEqual(data['timezone'],'Asia/Shanghai')

    def test_completed_days_are_cached_but_today_is_live(self):
        with patch.object(app.time, 'time', return_value=self.NOW), patch.object(app, 'current_configuration', return_value=(0,[project()])), patch.object(app, 'request', side_effect=self.fixture) as upstream:
            first = app.usage_statistics('7d')
            self.assertEqual(upstream.call_count, 8)
            second = app.usage_statistics('7d')
            self.assertEqual(upstream.call_count, 12)
            self.assertEqual(first, second)
            self.assertTrue(all(call.kwargs['timeout'] == 5 for call in upstream.call_args_list))
        with patch.object(app.time, 'time', return_value=self.NOW+3600), patch.object(app, 'current_configuration', return_value=(0,[project()])), patch.object(app, 'request', side_effect=self.fixture) as upstream:
            app.usage_statistics('7d')
            self.assertEqual(upstream.call_count, 8)

    def test_alias_changes_display_only(self):
        config = project()
        config['alias'] = '主力模型'
        with patch.object(app.time,'time',return_value=self.NOW), patch.object(app,'current_configuration',return_value=(0,[config])), patch.object(app,'request',side_effect=self.fixture):
            data = app.usage_statistics('7d')
        self.assertEqual(data['rows'][0]['display_model'], '主力模型')
        self.assertEqual(data['rows'][0]['model'], 'model-a')
        self.assertEqual(data['totals']['requests'], 21)

    def test_saving_alias_keeps_usage_and_scrape_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = app.ModelStore(Path(directory) / 'models.sqlite3')
            config = project()
            config["nodes"][0].pop("api_key_env")
            store.initialize(lambda: [config])
            with patch.object(app, 'STORE', store), patch.object(app.time, 'time', return_value=self.NOW), patch.object(app, 'request', side_effect=self.fixture):
                before = app.usage_statistics('7d')
                targets = app.targets()
                model = store.list_public()['models'][0]
                store.save(dict(name=model['name'], url=model['url'], version=model['version'], alias='主力 Qwen'), model['id'])
                after = app.usage_statistics('7d')
                self.assertEqual(app.targets(), targets)
                self.assertEqual(after['totals'], before['totals'])
                self.assertEqual(after['rows'][0]['daily'], before['rows'][0]['daily'])
                self.assertEqual(after['rows'][0]['display_model'], '主力 Qwen')
                self.assertEqual(after['rows'][0]['model'], 'model-a')

    def test_unobserved_endpoint_does_not_erase_known_totals(self):
        with patch.object(app.time,'time',return_value=self.NOW), patch.object(app,'current_configuration',return_value=(0,[project(),project('unobserved')])), patch.object(app,'request',side_effect=self.fixture):
            data=app.usage_statistics('7d')
        self.assertTrue(data['partial'])
        self.assertEqual(data['totals']['requests'],21)
        self.assertEqual(data['totals']['total_tokens'],210)
        self.assertIsNone(data['rows'][1]['requests'])

    def test_calendar_boundaries(self):
        from datetime import datetime, timezone, timedelta
        now=datetime(2026,9,22,12,tzinfo=timezone(timedelta(hours=8))).timestamp()
        periods=app.daily_periods('7d',now)
        self.assertEqual(periods[0][1],'2026-09-16')
        self.assertEqual(periods[-1][1],'2026-09-22')
        self.assertEqual(now-periods[-1][0],43200)
        self.assertEqual(len(app.daily_periods('24h',now)),1)

    def test_missing_days_remain_unknown(self):
        with patch.object(app.time,'time',return_value=self.NOW), patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',return_value=b'{"status":"success","data":{"result":[]}}'):
            data=app.usage_statistics('7d')
        self.assertTrue(data['partial'])
        self.assertIsNone(data['totals']['requests'])
        self.assertTrue(all(day['waiting'] is None for day in data['rows'][0]['daily']))

    def test_query_failure_is_not_zero_usage(self):
        with patch.object(app,'request',return_value=b'{"status":"error"}'), patch.object(app,'current_configuration',return_value=(0,[project()])):
            with self.assertRaises(ValueError):
                app.usage_statistics('7d')


class AverageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, STATISTICS_CACHE_DB=str(Path(directory.name)/'cache.sqlite3'))
        env.start()
        self.addCleanup(env.stop)

    def test_output_speed_uses_request_tpot_not_token_counter(self):
        output = app.average_queries('12h')['output_average']
        self.assertIn('histogram_quantile(0.95', output)
        self.assertIn('request_time_per_output_token_seconds_bucket', output)
        self.assertNotIn('generation_tokens_total', output)
        self.assertNotIn('inter_token_latency_seconds', output)
        self.assertIn('prompt_tokens_total', app.average_queries('12h')['input_average'])

    def test_nonzero_selection_precedes_average_and_keeps_labels(self):
        queries = app.average_queries('12h')
        for direction in ('input', 'output'):
            expr = queries[direction + '_average']
            self.assertIn('avg_over_time(', expr)
            self.assertIn(' > 0)[12h:3s]', expr)
            self.assertNotIn('bool', expr)
            self.assertIn('monitor_project,monitor_node,model_name', expr)
            self.assertIn('timestamp(', expr)
            self.assertIn(' == 1)', expr)
            self.assertIn(' >= 0)', queries[direction + '_observed_seconds'])

    def test_idle_has_zero_active_time_but_missing_is_unknown(self):
        def request(url, **kwargs):
            expr = parse_qs(urlsplit(url).query)['query'][0]
            results = []
            if '>= 0' in expr:
                results = [{'metric': {'monitor_project': 'one', 'monitor_node': 'engine', 'model_name': 'idle'},
                            'value': [1, '43200']}]
            return json.dumps({'status':'success','data':{'result':results}}).encode()
        with patch.object(app, 'current_configuration', return_value=(0, [project(), project('two')])), patch.object(app, 'request', side_effect=request):
            data = app.throughput_averages('12h')
        idle, missing = data['rows']
        self.assertIsNone(idle['output_average'])
        self.assertEqual(idle['output_active_seconds'], 0)
        self.assertEqual(idle['output_observed_seconds'], 43200)
        self.assertIsNone(missing['output_active_seconds'])
        self.assertIsNone(missing['output_observed_seconds'])

    def test_average_endpoint_rejects_unbounded_windows(self):
        with self.assertRaises(ValueError):
            app.throughput_averages('30d')

    def test_average_presets_and_custom_bounds(self):
        with patch.object(app.time, 'time', return_value=2000000000):
            for window, seconds in app.AVERAGE_WINDOWS.items():
                start, end = app.average_range(window)
                self.assertEqual(end-start, seconds)
            self.assertEqual(app.average_range('custom', '1999996400', '2000000000'), (1999996400, 2000000000))
            for start, end in [(None,None), ('nan','inf'), (2000000000,1999999999), (1,2000000000), (1999999999,2000000001)]:
                with self.assertRaises(ValueError):
                    app.average_range('custom', start, end)

    def test_custom_average_query_uses_selected_end_and_duration(self):
        urls = []
        def request(url, **kwargs):
            urls.append(parse_qs(urlsplit(url).query))
            return b'{"status":"success","data":{"result":[]}}'
        with patch.object(app, 'current_configuration', return_value=(0, [])), patch.object(app, 'request', side_effect=request):
            data = app.throughput_averages('custom', 1700000000, 1700021600)
        self.assertEqual(data['duration_seconds'], 21600)
        self.assertEqual(data['start'], 1700000000)
        for params in urls:
            self.assertEqual(params['time'], ['1700021600'])
            self.assertIn('[21600s:3s]', params['query'][0])

    def latency_fixture(self, observations, calls):
        def request(url, **kwargs):
            params = parse_qs(urlsplit(url).query)
            calls.append(params)
            self.assertEqual(kwargs['timeout'], 5)
            self.assertEqual(params['timeout'], ['4s'])
            resolution = int(params['step'][0])
            self.assertIn(resolution,[3,60])
            begin, end = int(params['start'][0]), int(params['end'][0])
            labels = {'monitor_project':'one','monitor_node':'engine','model_name':'m'}
            if resolution==3:
                points = [[t,str(v)] for t,v in observations if begin<=t<=end]
            else:
                expression = params['query'][0]
                self.assertIn('[60s:3s]',expression)
                points=[]
                for t in range(begin,end+1,60):
                    values=[v for stamp,v in observations if t-60<stamp<=t]
                    valid=[v for v in values if v>=0]
                    if '== bool 0' in expression:
                        if values: points.append([t,str(values.count(-1))])
                    elif valid:
                        points.append([t,str(len(valid) if expression.startswith('count_over_time') else sum(valid))])
            return json.dumps({'status':'success','data':{'result':[{'metric':labels,'values':points}]}}).encode()
        return request

    def test_latency_means_preserve_weighting_gaps_and_zero_values(self):
        start = 1700000040
        points = [[start+3,.02],[start+6,.04],[start+123,.06],[start+180,0]]
        calls = []
        with patch.object(app,'current_configuration',return_value=(0,[project(),project('two')])), patch.object(app,'request',side_effect=self.latency_fixture(points,calls)):
            data = app.latency_averages('custom', start, start+180)
        row, missing = data['rows']
        self.assertAlmostEqual(row['tpot']['average'],.03)
        self.assertEqual(row['tpot']['observed_seconds'],12)
        self.assertEqual(row['tpot']['samples'], [[start+60,.03],[start+120,None],[start+180,.03]])
        self.assertIsNone(missing['tpot']['average'])
        self.assertEqual(len(calls),6)

    def test_partial_bucket_and_repeated_query_reuse_persistent_history(self):
        start = 1700000040
        points = [[start+i,.02] for i in range(3,61,3)] + [[start+63,.08]]
        calls = []
        with patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',side_effect=self.latency_fixture(points,calls)):
            first = app.latency_averages('custom',start,start+63)
            second = app.latency_averages('custom',start,start+63)
        self.assertEqual(first,second)
        self.assertEqual(len(calls),8)
        self.assertAlmostEqual(first['rows'][0]['tpot']['average'],(20*.02+.08)/21)
        self.assertEqual(first['rows'][0]['tpot']['observed_seconds'],63)
        self.assertEqual(first['rows'][0]['tpot']['samples'][-1][0],start+63)
        self.assertAlmostEqual(first['rows'][0]['tpot']['samples'][-1][1],.08)

    def test_only_fully_observed_idle_buckets_can_be_connected(self):
        start = 1700000040
        points = [[start+i,-1] for i in range(3,121,3) if i!=63] + [[start+180,.02]]
        calls = []
        with patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',side_effect=self.latency_fixture(points,calls)):
            data = app.latency_averages('custom',start,start+180)
        for metric in ('tpot','ttft'):
            self.assertEqual(data['rows'][0][metric]['states'],[[start+60,'idle'],[start+120,'missing'],[start+180,'observed']])
            self.assertEqual(data['rows'][0][metric]['observed_seconds'],3)

    def test_history_tiles_only_fetch_new_tail_and_do_not_duplicate_boundaries(self):
        start = 1700000040//86400*86400
        points = [[start+86400,.02],[start+86403,.04],[start+86406,.06]]
        calls = []
        with patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',side_effect=self.latency_fixture(points,calls)):
            app.latency_averages('custom',start,start+86403)
            calls.clear()
            result = app.latency_averages('custom',start,start+86406)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(p['start']==[str(start+86406)] for p in calls))
        self.assertEqual(result['rows'][0]['tpot']['observed_seconds'],9)
        self.assertAlmostEqual(result['rows'][0]['tpot']['average'],.04)

    def test_recent_minute_is_reread_and_short_ranges_do_not_fetch_old_tiles(self):
        now = 1700000010
        calls=[]
        with patch.object(app.time,'time',return_value=now), patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',side_effect=self.latency_fixture([[now,.02]],calls)):
            app.latency_averages('custom',now-3,now)
            app.latency_averages('custom',now-3,now)
        self.assertEqual(len(calls),4)
        self.assertTrue(all(p['start']==[str(now)] for p in calls))

    def test_unaligned_custom_bounds_exclude_neighbors_and_keep_partial_minutes(self):
        start,end=1700000047,1700000185
        observations=[[t, .02+(t%12)*.001] for t in range(1700000040,1700000191,3)]
        observations[0][1]=999
        calls=[]
        with patch.object(app,'current_configuration',return_value=(0,[project()])), patch.object(app,'request',side_effect=self.latency_fixture(observations,calls)):
            result=app.latency_averages('custom',start,end)['rows'][0]['tpot']
        expected=[v for t,v in observations if start<t<=end]
        self.assertAlmostEqual(result['average'],sum(expected)/len(expected))
        self.assertEqual(result['observed_seconds'],len(expected)*3)
        self.assertEqual(result['samples'][-1][0],end)
        previous=start
        for t,value in result['samples']:
            values=[v for stamp,v in observations if previous<stamp<=t]
            self.assertAlmostEqual(value,sum(values)/len(values))
            previous=t
