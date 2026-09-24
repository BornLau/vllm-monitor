import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from test_app import app, project


class StatisticsTests(unittest.TestCase):
    NOW = 1789984800

    def fixture(self, url):
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
        def request(url):
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
        def request(url):
            urls.append(parse_qs(urlsplit(url).query))
            return b'{"status":"success","data":{"result":[]}}'
        with patch.object(app, 'current_configuration', return_value=(0, [])), patch.object(app, 'request', side_effect=request):
            data = app.throughput_averages('custom', 1700000000, 1700021600)
        self.assertEqual(data['duration_seconds'], 21600)
        self.assertEqual(data['start'], 1700000000)
        for params in urls:
            self.assertEqual(params['time'], ['1700021600'])
            self.assertIn('[21600s:3s]', params['query'][0])

    def test_latency_means_use_full_resolution_and_history_preserves_gaps(self):
        urls = []
        def request(url):
            params = parse_qs(urlsplit(url).query)
            urls.append(params)
            labels = {'monitor_project':'one','monitor_node':'engine','model_name':'m'}
            if 'query_range' in url:
                result = {'metric':labels,'values':[[1700000120,'0.02'],[1700000240,'NaN'],[1700000480,'0.04']]}
            else:
                result = {'metric':labels,'value':[1700021600,'300' if 'count_over_time' in params['query'][0] else '0.025']}
            return json.dumps({'status':'success','data':{'result':[result]}}).encode()
        with patch.object(app, 'current_configuration', return_value=(0,[project(),project('two')])), patch.object(app,'request',side_effect=request):
            data=app.latency_averages('custom',1700000000,1700021600)
        self.assertEqual(data['step_seconds'],120)
        row, missing=data['rows']
        self.assertEqual(row['tpot']['average'],.025)
        self.assertEqual(row['ttft']['observed_seconds'],900)
        self.assertEqual(row['tpot']['samples'][:4],[[1700000120,.02],[1700000240,None],[1700000360,None],[1700000480,.04]])
        self.assertIsNone(missing['tpot']['average'])
        for params in urls:
            expr=params['query'][0]
            self.assertIn('model_name',expr)
            self.assertIn('timestamp(',expr)
            self.assertIn(' == 1)',expr)
            if 'start' in params:
                self.assertIn('avg_over_time(', expr)
                self.assertIn('[120s:3s]', expr)
                self.assertEqual(params['start'], ['1700000120'])
            if 'time' in params:
                self.assertIn('[21600s:3s]',expr)
                self.assertEqual(params['time'],['1700021600'])
        for expr in app.latency_queries().values():
            self.assertNotIn('histogram_quantile',expr)
            self.assertIn('_sum',expr)
            self.assertIn('_count',expr)
            self.assertIn('irate(',expr)

    def test_latency_buckets_include_partial_end_and_use_five_minutes_for_day(self):
        urls = []
        def request(url):
            params = parse_qs(urlsplit(url).query)
            urls.append(params)
            labels = {'monitor_project':'one','monitor_node':'engine','model_name':'m'}
            if 'start' in params:
                result = [{'metric':labels,'values':[[int(params['start'][0]), '0.02']]}]
            else:
                result = [{'metric':labels,'value':[int(params['time'][0]), '0.04']}]
            return json.dumps({'status':'success','data':{'result':result}}).encode()
        with patch.object(app, 'current_configuration', return_value=(0,[project()])), patch.object(app, 'request', side_effect=request):
            data = app.latency_averages('custom',1700000000,1700086401)
        self.assertEqual(data['step_seconds'], 360)
        self.assertEqual(data['rows'][0]['tpot']['samples'][0], [1700000360, .02])
        self.assertEqual(data['rows'][0]['tpot']['samples'][-1], [1700086401, .04])
        self.assertTrue(any('[1s:3s]' in params['query'][0] for params in urls))
        with patch.object(app, 'current_configuration', return_value=(0,[])), patch.object(app, 'request', side_effect=request):
            data = app.latency_averages('custom',1700000000,1700086400)
        self.assertEqual(data['step_seconds'], 300)
