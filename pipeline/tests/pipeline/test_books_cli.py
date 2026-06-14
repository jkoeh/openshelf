from __future__ import annotations

import argparse
import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline import books  # noqa: E402
from openshelf.pipeline.cli import main as cli_main  # noqa: E402
from openshelf.pipeline.ops.gpu_preflight import (  # noqa: E402
    GpuPreflightReport,
    PreflightMessage,
    TorchStatus,
)


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


class TestBooksCli(unittest.TestCase):
    def test_process_command_includes_resolved_device_and_upload(self):
        cmd = books.build_process_command(_args(), "cuda", skip_preflight=True)

        self.assertIn("--device", cmd)
        self.assertIn("cuda", cmd)
        self.assertIn("--upload", cmd)
        self.assertIn("--voice", cmd)
        self.assertIn("chatterbox-bf_emma", cmd)
        self.assertIn("--skip-preflight", cmd)
        self.assertIn("openshelf.pipeline.cli", cmd)

    @patch("openshelf.pipeline.books.refresh_catalog", return_value=0)
    @patch("openshelf.pipeline.books.convert_book", return_value=0)
    @patch("openshelf.pipeline.books.configure_pipeline_logging", return_value="test.log")
    @patch("openshelf.pipeline.books.configure_console_output")
    @patch("openshelf.pipeline.books.run_gpu_preflight")
    def test_books_process_uses_preflight_resolved_device(
        self,
        preflight,
        _console,
        _logging,
        convert_book,
        _refresh,
    ):
        preflight.return_value = _report(True, "cuda")

        exit_code = books.process_books(_args(epub=__file__))

        self.assertEqual(exit_code, 0)
        args = convert_book.call_args.args[2]
        self.assertEqual(args.device, "cuda")

    @patch("openshelf.pipeline.books.convert_book")
    @patch("openshelf.pipeline.books.run_gpu_preflight")
    def test_books_process_stops_when_preflight_fails(self, preflight, convert_book):
        preflight.return_value = _report(False, "cpu")

        with self.assertRaises(SystemExit) as raised:
            books.process_books(_args(epub=__file__))

        self.assertEqual(raised.exception.code, 2)
        convert_book.assert_not_called()

    def test_books_process_requires_book_selector(self):
        with self.assertRaises(ValueError):
            books.process_books(_args(epub=None, author=None, book=None))

    @patch("openshelf.pipeline.books.main", return_value=0)
    def test_root_dispatches_books(self, books_main):
        exit_code = cli_main(["books", "process", "--epub", "book.epub", "--dry-run"])

        self.assertEqual(exit_code, 0)
        books_main.assert_called_once_with(["process", "--epub", "book.epub", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
