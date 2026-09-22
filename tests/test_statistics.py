import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from test_app import app, project


class StatisticsTests(unittest.TestCase):
    def test_model_labels_totals_and_counter_queries(self):
        def request(url):
            params = parse_qs(urlsplit(url).query)
            expr = params['query'][0]
            self.assertIn('sum by (monitor_project,monitor_node,model_name) (increase(', expr)
            self.assertIn('[7d]', expr)
            self.assertIn('time', params)
            value = 3 if 'request_success_total' in expr else 10 if 'prompt_tokens_total' in expr else 20
            result = [{'metric': {'monitor_project': 'one', 'monitor_node': 'engine', 'model_name': model},
                       'value': [1, str(value)]} for model in ('model-a', 'model-b')]
            result.append({'metric': {'monitor_project': 'removed', 'monitor_node': 'engine'}, 'value': [1, '999']})
            return json.dumps({'status': 'success', 'data': {'result': result}}).encode()
        with patch.object(app, 'current_configuration', return_value=(0, [project()])), patch.object(app, 'request', side_effect=request):
            data = app.usage_statistics('7d')
        self.assertEqual([r['model'] for r in data['rows']], ['model-a', 'model-b'])
        self.assertEqual(data['totals'], {'requests': 6, 'input_tokens': 20, 'output_tokens': 40, 'total_tokens': 60})

    def test_missing_metrics_do_not_become_zero(self):
        def request(url):
            result = [] if 'generation_tokens_total' in url else [
                {'metric': {'monitor_project': 'one', 'monitor_node': 'engine'}, 'value': [1, '0']}]
            return json.dumps({'status': 'success', 'data': {'result': result}}).encode()
        with patch.object(app, 'current_configuration', return_value=(0, [project()])), patch.object(app, 'request', side_effect=request):
            data = app.usage_statistics('30d')
        self.assertEqual(data['totals']['requests'], 0)
        self.assertIsNone(data['totals']['output_tokens'])
        self.assertIsNone(data['totals']['total_tokens'])
        self.assertEqual(data['rows'][0]['model'], '未提供 model_name')

    def test_query_failure_not_zero_usage(self):
        with patch.object(app, 'request', return_value=b'{"status":"error"}'), patch.object(app, 'current_configuration', return_value=(0, [project()])):
            with self.assertRaises(ValueError):
                app.usage_statistics('24h')

    def test_no_series_preserves_configured_endpoint(self):
        with patch.object(app, 'request', return_value=b'{"status":"success","data":{"result":[]}}'), patch.object(app, 'current_configuration', return_value=(0, [project()])):
            data = app.usage_statistics('24h')
        self.assertEqual(len(data['rows']), 1)
        self.assertTrue(all(v is None for v in data['totals'].values()))


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
