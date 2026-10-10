"""Offline integration tests for the local browser security boundary."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openshelf.pipeline import job_monitor as monitor
from openshelf.pipeline import job_monitor_web as web
from pipeline.tests.pipeline.test_job_monitor import job


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.api = Mock()
        self.api.list_jobs.return_value = ([monitor.MonitoredJob.from_api(job())], [])
        self.find = patch.object(monitor, "find_consumer", return_value=None)
        self.find.start()
        self.server = web.DashboardServer(self.api, self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.find.stop()
        self.tmp.cleanup()

    def request(self, path="/api/status", method="GET", payload=None, **headers):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        defaults = {"X-Monitor-Token": self.server.token}
        if method == "POST":
            defaults.update({"Origin": self.server.origin, "Content-Type": "application/json"})
        defaults.update(headers)
        connection.request(method, path, json.dumps(payload) if payload is not None else None, defaults)
        response = connection.getresponse()
        status, body, response_headers = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return status, body, response_headers

    def test_loopback_and_authentication(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["active"][0]["title"], "Alice")
        self.assertNotIn(self.server.token.encode(), body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request(**{"X-Monitor-Token": "wrong"})[0], 401)
        self.api.list_jobs.reset_mock()
        self.assertEqual(self.request(**{"X-Monitor-Token": ""})[0], 401)
        self.api.list_jobs.assert_not_called()

    def test_dns_rebinding_and_cross_origin_rejected_before_access(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(**headers)[0], 403)
        self.api.list_jobs.assert_not_called()
        self.assertEqual(self.request(method="OPTIONS")[0], 403)

    def test_static_assets_are_allowlisted_and_framing_blocked(self):
        for path in ("/", "/dashboard.js", "/dashboard.css"):
            status, body, headers = self.request(path, **{"X-Monitor-Token": ""})
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertNotIn(self.server.token.encode(), body)
        for path in ("/../../worker/.secrets/prod-owner-token", "/pipeline/.env", "/?token=anything"):
            self.assertEqual(self.request(path)[0], 404)

    def test_mutations_require_origin_token_json_and_small_object(self):
        payload = {"id": job()["id"], "action": "cancel"}
        for headers in ({"Origin": ""}, {"Origin": "https://evil.example"},
                        {"X-Monitor-Token": ""}, {"Content-Type": "text/plain"},
                        {"Content-Type": "application/x-www-form-urlencoded"}):
            self.assertIn(self.request("/api/job", "POST", payload, **headers)[0], (401, 403))
        self.assertEqual(self.request("/api/job", "POST", ["not an object"])[0], 400)
        self.assertEqual(self.request("/api/job", "POST", {"pad": "x" * 4096})[0], 400)
        self.api.cancel.assert_not_called()

    def test_action_validation_and_state_checks(self):
        job_id = job()["id"]
        self.assertEqual(self.request("/api/job", "POST", {"id": job_id, "action": "high"})[0], 200)
        self.api.set_priority.assert_called_once_with(job_id, True)
        self.assertEqual(self.request("/api/job", "POST", {"id": job_id, "action": "cancel"})[0], 200)
        self.api.cancel.assert_called_once_with(job_id)
        self.assertEqual(self.request("/api/job", "POST", {"id": job_id, "action": "retry"})[0], 409)
        self.assertEqual(self.request("/api/job", "POST", {"id": "../../secret", "action": "cancel"})[0], 400)
        self.assertEqual(self.request("/api/job", "POST", {"id": job_id, "action": "shell"})[0], 409)
        self.api.list_jobs.return_value = ([], [monitor.MonitoredJob.from_api(job(state="failed"))])
        self.assertEqual(self.request("/api/job", "POST", {"id": job_id, "action": "retry"})[0], 200)
        self.api.retry.assert_called_once_with(job_id)

    def test_log_redaction_and_no_exception_leak(self):
        directory = self.root / "worker" / ".secrets"
        directory.mkdir(parents=True)
        secret = "owner-token-that-must-never-leak"
        (directory / "prod-owner-token").write_text(secret)
        with patch.object(monitor, "log_tail", return_value=f"secret {secret}"), patch.dict(
                web.os.environ, {"OPENAI_API_KEY": "private-openai-key"}):
            self.assertNotIn(secret, self.request()[1].decode())
            self.assertEqual(web.redact("private-openai-key", self.root), "[redacted]")
        self.api.list_jobs.side_effect = RuntimeError(secret)
        status, body, _ = self.request()
        self.assertEqual(status, 500)
        self.assertNotIn(secret.encode(), body)

    def test_start_consumer_and_unknown_route(self):
        with patch.object(monitor, "start_consumer", return_value=123) as start:
            self.assertEqual(self.request("/api/consumer/start", "POST", {})[0], 200)
            start.assert_called_once_with(self.root)
        self.assertEqual(self.request("/api/execute", "POST", {})[0], 400)


class StopTests(unittest.TestCase):
    def test_busy_consumer_is_not_terminated(self):
        consumer, child = Mock(pid=10), Mock(pid=11)
        consumer.children.return_value = [child]
        consumer.cmdline.return_value = ["consume-jobs", monitor.API_BASE]
        child.cmdline.return_value = ["books", "process"]
        with patch.object(monitor, "find_consumer", return_value=consumer):
            with self.assertRaises(monitor.MonitorError):
                web.stop_idle_consumer(Mock(), Path("."))
        consumer.terminate.assert_not_called()
        child.terminate.assert_not_called()

    def test_queue_unavailable_resumes_consumer_and_fails_closed(self):
        consumer = Mock(pid=10)
        consumer.children.return_value = []
        consumer.cmdline.return_value = ["consume-jobs", monitor.API_BASE]
        api = Mock()
        api.list_jobs.side_effect = monitor.MonitorError("Offline")
        with patch.object(monitor, "find_consumer", return_value=consumer):
            with self.assertRaises(monitor.MonitorError):
                web.stop_idle_consumer(api, Path("."))
        consumer.suspend.assert_called_once()
        consumer.resume.assert_called_once()
        consumer.terminate.assert_not_called()

    def test_idle_consumer_stops_and_running_job_blocks(self):
        consumer = Mock(pid=10)
        consumer.children.return_value = []
        consumer.cmdline.return_value = ["consume-jobs", monitor.API_BASE]
        api = Mock()
        api.list_jobs.return_value = ([], [])
        with patch.object(monitor, "find_consumer", return_value=consumer):
            web.stop_idle_consumer(api, Path("."))
            consumer.terminate.assert_called_once()
            consumer.reset_mock()
            api.list_jobs.return_value = ([SimpleNamespace(state="running")], [])
            with self.assertRaises(monitor.MonitorError):
                web.stop_idle_consumer(api, Path("."))
            consumer.terminate.assert_not_called()
            consumer.resume.assert_called_once()


if __name__ == "__main__":
    unittest.main()
