"""Tests for local pipeline logging helpers."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.logging_utils import (  # noqa: E402
    Heartbeat,
    close_pipeline_logging,
    configure_pipeline_logging,
    safe_log_name,
)


class TestSafeLogName(unittest.TestCase):
    def test_slugifies_run_name(self):
        self.assertEqual(safe_log_name("Alice's Adventures / Build 1"), "alice-s-adventures-build-1")

    def test_empty_fallback(self):
        self.assertEqual(safe_log_name("   "), "pipeline")


class TestConfigurePipelineLogging(unittest.TestCase):
    def test_creates_log_file_and_writes_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                path = configure_pipeline_logging("unit test", tmp)
                logger = logging.getLogger("openshelf.test")
                logger.info("hello logging")
                for handler in logging.getLogger().handlers:
                    handler.flush()

                self.assertTrue(path.exists())
                text = path.read_text(encoding="utf-8")
                self.assertIn("hello logging", text)
                self.assertIn("openshelf.test", text)
            finally:
                close_pipeline_logging()

    def test_replaces_previous_pipeline_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                first = configure_pipeline_logging("first", tmp)
                second = configure_pipeline_logging("second", tmp)
                logger = logging.getLogger("openshelf.test.replace")
                logger.info("only second")
                for handler in logging.getLogger().handlers:
                    handler.flush()

                self.assertNotEqual(first, second)
                self.assertNotIn("only second", first.read_text(encoding="utf-8"))
                self.assertIn("only second", second.read_text(encoding="utf-8"))
            finally:
                close_pipeline_logging()


class TestHeartbeat(unittest.TestCase):
    def test_logs_start_progress_heartbeat_and_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                path = configure_pipeline_logging("heartbeat", tmp)
                logger = logging.getLogger("openshelf.test.heartbeat")

                with Heartbeat(logger, "Long stage", interval_seconds=0) as heartbeat:
                    heartbeat.update("Long stage chunk 1/2")
                    heartbeat.beat("manual check")

                for handler in logging.getLogger().handlers:
                    handler.flush()
                text = path.read_text(encoding="utf-8")
            finally:
                close_pipeline_logging()

        self.assertIn("Started: Long stage", text)
        self.assertIn("Progress: Long stage chunk 1/2", text)
        self.assertIn("manual check", text)
        self.assertIn("Finished: Long stage chunk 1/2", text)


if __name__ == "__main__":
    unittest.main()
