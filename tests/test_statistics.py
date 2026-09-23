import json
import unittest
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
