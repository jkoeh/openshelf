from __future__ import annotations

import argparse
import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.gpu_preflight import (  # noqa: E402
    GpuPreflightReport,
    PreflightMessage,
    TorchStatus,
)
from openshelf.pipeline.pipeline_runner import build_process_books_command, run_pipeline  # noqa: E402


def _args(**overrides):
    values = {
        "author": None,
        "book": None,
        "epub": "book.epub",
        "source": "all",
        "output": "audio",
        "engine": "chatterbox",
        "voice": "chatterbox-bf_emma",
        "rendition": None,
        "cast_mode": "solo",
        "performance_direction": "batched",
        "device": "auto",
        "chapters": None,
        "dry_run": False,
        "keep_wav": False,
        "upload": True,
        "build_id": None,
        "resume": False,
        "force": False,
        "delay": 2,
        "download_dir": "download/books",
        "log_dir": "logs",
        "skip_preflight": False,
        "load_engine_preflight": False,
        "background": False,
        "name": None,
        "pid_file": None,
        "stdout_log": None,
        "stderr_log": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _report(ok: bool, resolved: str = "cuda"):
    return GpuPreflightReport(
        engine="chatterbox",
        requested_device="auto",
        resolved_device=resolved,
        accelerator_required=True,
        torch=TorchStatus(imported=True, cuda_available=(resolved == "cuda")),
        ok=ok,
        messages=[PreflightMessage("info" if ok else "error", "test")],
    )


class TestPipelineRunner(unittest.TestCase):
    def test_command_includes_resolved_device_and_upload(self):
        cmd = build_process_books_command(_args(), "cuda")

        self.assertIn("--device", cmd)
        self.assertIn("cuda", cmd)
        self.assertIn("--upload", cmd)
        self.assertIn("--voice", cmd)
        self.assertIn("chatterbox-bf_emma", cmd)

    @patch("openshelf.pipeline.pipeline_runner.subprocess.run")
    @patch("openshelf.pipeline.pipeline_runner.run_gpu_preflight")
    def test_run_pipeline_uses_preflight_resolved_device(self, preflight, run):
        preflight.return_value = _report(True, "cuda")
        run.return_value.returncode = 0

        exit_code = run_pipeline(_args())

        self.assertEqual(exit_code, 0)
        called_cmd = run.call_args.args[0]
        self.assertIn("--device", called_cmd)
        self.assertIn("cuda", called_cmd)

    @patch("openshelf.pipeline.pipeline_runner.subprocess.run")
    @patch("openshelf.pipeline.pipeline_runner.run_gpu_preflight")
    def test_run_pipeline_stops_when_preflight_fails(self, preflight, run):
        preflight.return_value = _report(False, "cpu")

        exit_code = run_pipeline(_args())

        self.assertEqual(exit_code, 2)
        run.assert_not_called()

    def test_run_pipeline_requires_book_selector(self):
        with self.assertRaises(ValueError):
            run_pipeline(_args(epub=None, author=None, book=None))


if __name__ == "__main__":
    unittest.main()
