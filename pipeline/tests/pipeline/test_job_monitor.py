"""Offline tests for the local queue monitor and consumer launcher."""

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openshelf.pipeline import job_monitor as monitor


def job(**changes):
    data = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source_id": "gutenberg:11", "title": "Alice", "author": "Lewis Carroll",
        "mode": "standard", "state": "queued", "stage": "queued", "priority": 0,
        "attempts": 0, "created_at": "2026-10-05T10:00:00Z",
        "updated_at": "2026-10-05T10:00:00Z", "lease_until": None,
        "error_code": None,
    }
    data.update(changes)
    return data


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class MonitorTests(unittest.TestCase):
    def test_stuck_requires_expired_lease_not_just_an_old_stage(self):
        current = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
        running = monitor.MonitoredJob.from_api(job(
            state="running", stage="synthesis", updated_at="2026-10-05T11:59:30Z",
            lease_until="2026-10-05T12:01:00Z"))
        self.assertEqual(running.status(current), "Working")
        self.assertEqual(monitor.MonitoredJob.from_api(job(
            state="running", lease_until="2026-10-05T11:59:59Z")).status(current),
            "Stuck · lease expired")
        self.assertEqual(monitor.age("2026-10-05T11:59:30Z", current), "30s")

    def test_owner_api_reads_local_token_and_sends_fixed_bounded_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / "worker" / ".secrets" / "prod-owner-token"
            token_path.parent.mkdir(parents=True)
            token_path.write_text("owner-token-longer-than-24-characters", encoding="utf-8")
            calls = []

            def open_request(request, timeout):
                calls.append(request)
                self.assertEqual(timeout, 10)
                self.assertEqual(request.get_header("Authorization"), "Bearer owner-token-longer-than-24-characters")
                self.assertTrue(request.get_header("User-agent").startswith("OpenShelf/1.0"))
                if request.get_method() == "GET":
                    return Response(json.dumps({"active": [job()], "recent": []}).encode())
                return Response(json.dumps(job()).encode())

            api = monitor.QueueAPI(root)
            with patch.object(monitor.urllib.request, "build_opener", return_value=SimpleNamespace(open=open_request)) as build_opener:
                active, recent = api.list_jobs()
                api.set_priority(active[0].id, True)
                api.cancel(active[0].id)
                api.retry(active[0].id)
            self.assertIsInstance(build_opener.call_args.args[0], monitor._NoRedirect)
            self.assertEqual(active[0].title, "Alice")
            self.assertEqual(recent, [])
            self.assertEqual(calls[0].full_url, monitor.API_BASE + "/admin/generation-jobs")
            self.assertEqual(json.loads(calls[1].data), {"priority": "high"})
            self.assertEqual(calls[2].full_url, monitor.API_BASE + f"/generation-jobs/{active[0].id}/cancel")
            self.assertEqual(calls[3].full_url, monitor.API_BASE + f"/generation-jobs/{active[0].id}/retry")
            self.assertNotIn(b"owner-token", calls[1].data)

    def test_missing_owner_token_fails_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(monitor.urllib.request, "build_opener") as build_opener:
                with self.assertRaises(monitor.MonitorError):
                    monitor.QueueAPI(Path(directory)).list_jobs()
                build_opener.assert_not_called()

    def test_find_consumer_matches_a_relative_launch_but_not_an_unrelated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "pipeline" / "scripts" / "openshelf-pipeline.py"
            script.parent.mkdir(parents=True)
            script.touch()
            wrong = SimpleNamespace(info={"cmdline": ["python", "other.py", "books", "consume-jobs", monitor.API_BASE]},
                                    cwd=lambda: str(root))
            actual = SimpleNamespace(info={"cmdline": ["python", "pipeline/scripts/openshelf-pipeline.py", "books", "consume-jobs", "--api-base", monitor.API_BASE]},
                                     cwd=lambda: str(root), pid=1234)
            with patch.object(monitor.psutil, "process_iter", return_value=[wrong, actual]):
                self.assertIs(monitor.find_consumer(root), actual)

    def test_start_consumer_passes_pc_secret_only_in_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / (".venv/Scripts/python.exe" if monitor.os.name == "nt" else ".venv/bin/python")
            executable.parent.mkdir(parents=True)
            executable.touch()
            token_path = root / "worker" / ".secrets" / "prod-pc-token"
            token_path.parent.mkdir(parents=True)
            token_path.write_text("pc-token-longer-than-24-characters", encoding="utf-8")
            with (patch.object(monitor, "find_consumer", return_value=None),
                  patch.object(monitor.shutil, "which", return_value="/ffmpeg"),
                  patch.object(monitor, "R2_ACCOUNT_ID", "account"),
                  patch.object(monitor, "R2_ACCESS_KEY", "access"),
                  patch.object(monitor, "R2_SECRET_KEY", "secret"),
                  patch.object(monitor.subprocess, "Popen", return_value=SimpleNamespace(pid=1234)) as popen):
                self.assertEqual(monitor.start_consumer(root), 1234)
            args, kwargs = popen.call_args
            self.assertIn("consume-jobs", args[0])
            self.assertNotIn("pc-token", " ".join(args[0]))
            self.assertEqual(kwargs["env"]["OPENSHELF_PC_TOKEN"], "pc-token-longer-than-24-characters")
            self.assertEqual(kwargs["env"]["R2_BUCKET"], "openshelf")
            self.assertEqual((root / "audio" / "consumer-production.pid").read_text(), "1234")


if __name__ == "__main__":
    unittest.main()
