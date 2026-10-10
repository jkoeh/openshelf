"""Offline desktop lifecycle and credential-boundary tests."""
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openshelf.pipeline import job_monitor as monitor
from openshelf.pipeline import job_monitor_desktop as desktop


class DesktopTests(unittest.TestCase):
    def test_native_auth_only_reaches_local_api_from_same_origin(self):
        origin, token = "http://127.0.0.1:12345", "private-session-token"
        for url, headers, expected in (
            (origin + "/api/status", {}, True),
            (origin + "/api/close", {"Origin": origin, "Sec-Fetch-Site": "same-origin"}, True),
            (origin + "/dashboard.js", {}, False),
            ("https://evil.example/api/status", {}, False),
            (origin + ".evil.example/api/status", {}, False),
            ("http://127.0.0.1:12346/api/status", {}, False),
            (origin + "/api/status", {"Origin": "https://evil.example"}, False),
            (origin + "/api/status", {"Sec-Fetch-Site": "cross-site"}, False),
        ):
            with self.subTest(url=url, headers=headers):
                request = SimpleNamespace(url=url, headers=headers.copy())
                desktop.authenticate_request(request, origin, token)
                self.assertEqual(request.headers.get("X-Monitor-Token") == token, expected)

    def test_empty_frontend_token_is_replaced_without_duplicate_headers(self):
        request = SimpleNamespace(url="http://127.0.0.1:12345/api/status",
                                  headers={"x-monitor-token": ""})
        desktop.authenticate_request(request, "http://127.0.0.1:12345", "secret")
        self.assertEqual(request.headers, {"X-Monitor-Token": "secret"})

    def test_launch_adopts_process_and_close_stops_job_and_server_once(self):
        server, job, process = Mock(), Mock(), Mock()
        server.serve_forever.side_effect = lambda: event.wait(5)
        server.shutdown.side_effect = lambda: event.set()
        event = threading.Event()
        studio = desktop.Studio(root=Path("."), job=job, server=server)
        with patch.object(monitor, "start_consumer", return_value=123) as start, patch.object(
                desktop.psutil, "Process", return_value=process):
            studio.start()
            job.attach.assert_called_once_with(process)
            start.assert_called_once_with(Path("."))
        studio.close()
        studio.close()
        job.close.assert_called_once()
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        self.assertFalse(studio.thread.is_alive())
        with self.assertRaises(monitor.MonitorError):
            studio.start_consumer()

    def test_start_failure_still_cleans_resources(self):
        server, job = Mock(), Mock()
        studio = desktop.Studio(job=job, server=server)
        with patch.object(monitor, "start_consumer", side_effect=monitor.MonitorError("missing token")):
            with self.assertRaises(monitor.MonitorError):
                studio.start()
        studio.close()
        job.close.assert_called_once()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once()

    def test_server_creation_failure_closes_job_handle(self):
        job = Mock()
        with patch.object(desktop, "DashboardServer", side_effect=OSError("bind failed")):
            with self.assertRaises(OSError):
                desktop.Studio(job=job)
        job.close.assert_called_once()

    def test_windows_job_configures_kill_on_close_and_closes_handle(self):
        kernel = Mock()
        kernel.CreateJobObjectW.return_value = 123
        group = desktop.ConsumerJob(kernel)
        args = kernel.SetInformationJobObject.call_args.args
        self.assertEqual(args[:2], (123, 9))
        self.assertEqual(args[2]._obj.BasicLimits.LimitFlags, 0x2000)
        group.close()
        group.close()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_windows_job_attaches_root_and_existing_descendants(self):
        kernel = Mock()
        kernel.CreateJobObjectW.return_value = 123
        kernel.OpenProcess.return_value = 456
        group = desktop.ConsumerJob(kernel)
        process = Mock(pid=10)
        process.children.return_value = [SimpleNamespace(pid=11), SimpleNamespace(pid=12)]
        group.attach(process)
        self.assertEqual([call.args[2] for call in kernel.OpenProcess.call_args_list], [10, 11, 12])
        self.assertEqual(kernel.AssignProcessToJobObject.call_count, 3)
        group.close()

    def test_existing_child_can_have_a_separate_cleanup_group(self):
        kernel = Mock()
        kernel.CreateJobObjectW.side_effect = [123, 124]
        kernel.OpenProcess.return_value = 456
        kernel.AssignProcessToJobObject.side_effect = [True, False, True]
        group = desktop.ConsumerJob(kernel)
        process = Mock(pid=10)
        process.children.return_value = [SimpleNamespace(pid=11)]
        group.attach(process)
        self.assertEqual(group.extra_handles, [124])
        group.close()
        kernel.CloseHandle.assert_any_call(123)
        kernel.CloseHandle.assert_any_call(124)


if __name__ == "__main__":
    unittest.main()
