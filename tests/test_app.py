import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("app", Path(__file__).resolve().parents[1] / "dashboard/app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def project(pid="one"):
    return {"id": pid, "name": pid, "deployment": "standard", "nodes": [{"id": "engine", "role": "engine", "api_base_url": "http://localhost/v1", "api_key_env": "TEST_KEY", "metrics_url": "http://localhost/metrics"}]}


class MonitorTests(unittest.TestCase):
    def test_config_and_pd_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "services.json"
            self.assertEqual(app.load_config(path), [])
            p = project()
            path.write_text(json.dumps({"projects": [p]}))
            self.assertEqual(len(app.load_config(path)), 1)
            p["deployment"] = "pd"
            path.write_text(json.dumps({"projects": [p]}))
            with self.assertRaises(ValueError):
                app.load_config(path)
            p["deployment"] = "standard"
            p["nodes"][0]["metrics_url"] = "http://user:secret@localhost/metrics"
            path.write_text(json.dumps({"projects": [p]}))
            with self.assertRaises(ValueError):
                app.load_config(path)

    def test_missing_data_is_not_healthy(self):
        self.assertEqual(app.diagnose({})[0][0], "warning")
        self.assertEqual(app.diagnose({"up": 0})[0][0], "critical")
        self.assertEqual(app.diagnose({"up": 1})[0][1], "核心指标不完整")
        self.assertEqual(app.diagnose({"up": 1, "running": 0, "waiting": 0, "kv": 0})[0][0], "ok")
        self.assertEqual(app.diagnose({"up": 1, "waiting": 2, "kv": 95})[0][0], "critical")

    def test_queries_isolate_project_and_node(self):
        for expr in app.QUERIES.values():
            self.assertIn('job="vllm"', expr)
            self.assertIn("monitor_project,monitor_node", expr)
        payload = {"status": "success", "data": {"result": [{"metric": {"monitor_project": "one", "monitor_node": "engine"}, "value": [0, "3"]}, {"metric": {"monitor_project": "two", "monitor_node": "engine"}, "value": [0, "NaN"]}]}}
        with patch.object(app, "request", return_value=json.dumps(payload).encode()):
            self.assertEqual(app.query("x"), {("one", "engine"): 3, ("two", "engine"): None})

    def test_snapshot_isolation_and_down_hides_stale_data(self):
        def query(expr):
            if expr == app.QUERIES["up"]:
                return {("one", "engine"): 1, ("two", "engine"): 0}
            return {("one", "engine"): 7, ("two", "engine"): 123}
        with patch.object(app, "query", side_effect=query), patch.object(app, "probe", return_value={"state": "ok", "message": "ok"}):
            data = app.collect([project(), project("two")])
        self.assertEqual(data["projects"][0]["nodes"][0]["values"]["running"], 7)
        self.assertIsNone(data["projects"][1]["nodes"][0]["values"]["running"])
        self.assertNotIn("api_key_env", json.dumps(data))

    def test_prometheus_failure_surfaces(self):
        with patch.object(app, "query", side_effect=RuntimeError("secret upstream content")), patch.object(app, "probe", return_value={"state": "error", "message": "HTTP 401"}):
            data = app.collect([project()])
        self.assertEqual(len(data["errors"]), len(app.QUERIES))
        self.assertNotIn("secret", json.dumps(data))
        self.assertEqual(data["projects"][0]["nodes"][0]["findings"][0][0], "warning")

    def test_discovery_has_isolated_paths_and_no_credentials(self):
        with patch.object(app, "PROJECTS", [project(), project("two")]):
            targets = app.targets()
        self.assertEqual(targets[1]["labels"]["__metrics_path__"], "/scrape/two/engine")
        self.assertNotIn("TEST_KEY", json.dumps(targets))
        self.assertNotIn("localhost", json.dumps(targets))

    def test_report_utf8_limit(self):
        with patch.object(app, "query", return_value={}), patch.object(app, "probe", return_value={"state": "ok", "message": "ok"}):
            data = app.collect([project(str(i)) for i in range(40)])
        report = app.report(data)
        self.assertLessEqual(len(report), 10000)
        report.decode("utf-8")
        self.assertNotIn(b"http://", report)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.received = []
        owner = cls
        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.received.append((self.path, self.headers.get("Authorization")))
                if self.path == "/redirect/models":
                    self.send_response(302)
                    self.send_header("Location", "/v1/models")
                    self.end_headers()
                    return
                status = 401 if self.path == "/denied/models" else 200
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b'{"data": []}' if self.path.endswith("/models") else b'vllm:num_requests_running 4\n')
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_probe_auth_and_redirect(self):
        self.received.clear()
        with patch.dict(os.environ, {"TEST_KEY": "private-test-key"}):
            result = app.probe({"api_base_url": self.url + "/v1", "api_key_env": "TEST_KEY"})
            self.assertEqual(result["state"], "ok")
            self.assertEqual(self.received[-1], ("/v1/models", "Bearer private-test-key"))
            result = app.probe({"api_base_url": self.url + "/denied"})
            self.assertEqual(result["message"], "HTTP 401")
            before = len(self.received)
            result = app.probe({"api_base_url": self.url + "/redirect", "api_key_env": "TEST_KEY"})
            self.assertEqual(result["state"], "error")
            self.assertEqual(len(self.received), before + 1)
            self.assertNotIn("private-test-key", json.dumps(result))

    def test_metrics_proxy_with_separate_key(self):
        p = project()
        p["nodes"][0].update(metrics_url=self.url + "/metrics", metrics_key_env="METRIC_KEY")
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(app, "PROJECTS", [p]), patch.dict(os.environ, {"METRIC_KEY": "metrics-only"}):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/scrape/one/engine") as response:
                    self.assertIn(b"num_requests_running 4", response.read())
                self.assertEqual(self.received[-1], ("/metrics", "Bearer metrics-only"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
