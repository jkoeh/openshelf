"""Offline security and process-boundary tests for the PC job consumer."""

import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from openshelf.pipeline import job_consumer as consumer


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FakeAPI:
    def __init__(self):
        self.posts = []

    def post(self, path, payload=None):
        self.posts.append((path, payload))
        return {"ok": True}


class FakeChild:
    returncode = 0

    def __init__(self):
        self.polls = 0
        self.terminated = False

    def poll(self):
        self.polls += 1
        return None if self.polls == 1 else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.returncode = -15
        return self.returncode


class ConsumerTests(unittest.TestCase):
    def test_only_exact_gutenberg_https_url_is_accepted(self):
        good = "https://www.gutenberg.org/ebooks/11.epub3.images"
        self.assertEqual(consumer.validate_epub_url(good, "gutenberg:11"), good)
        for bad in ["http://www.gutenberg.org/ebooks/11.epub", "https://evil.example/ebooks/11.epub",
                    "https://www.gutenberg.org/ebooks/12.epub", "https://www.gutenberg.org.evil.example/ebooks/11.epub",
                    "https://www.gutenberg.org/ebooks/11.epub#fragment"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                consumer.validate_epub_url(bad, "gutenberg:11")
        with self.assertRaises(ValueError):
            consumer.GutenbergRedirects("gutenberg:11").redirect_request(
                None, None, 302, "redirect", {}, "https://127.0.0.1/ebooks/11.epub")

    def test_download_requires_real_epub_and_size_bound(self):
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as archive:
            archive.writestr("META-INF/container.xml", "<container/>")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "book.epub"
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = FakeResponse(memory.getvalue())
                consumer.fetch_epub("https://www.gutenberg.org/ebooks/11.epub", "gutenberg:11", target)
            self.assertTrue(zipfile.is_zipfile(target))
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = FakeResponse(b"<html>not an EPUB</html>")
                with self.assertRaises(ValueError):
                    consumer.fetch_epub("https://www.gutenberg.org/ebooks/11.epub", "gutenberg:11", target)
            self.assertTrue(target.exists())

    def test_exact_book_command_keeps_build_and_resumes_only_existing_run(self):
        job = {"id": "job-1", "source_id": "gutenberg:11", "title": "Alice", "author": "Lewis Carroll",
               "epub_url": "https://www.gutenberg.org/ebooks/11.epub", "build_id": "1234567890abcdef", "lease_token": "lease"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            epub = root / "download" / "gutenberg" / "lewis-carroll" / "alice-g11.epub"
            epub.parent.mkdir(parents=True)
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
            api = FakeAPI()
            child = FakeChild()
            with patch.object(consumer.subprocess, "Popen", return_value=child) as popen, patch.object(consumer.time, "sleep"):
                consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio", device="cuda")
            command = popen.call_args.args[0]
            self.assertIn("--epub", command)
            self.assertIn(str(epub), command)
            self.assertIn("1234567890abcdef", command)
            self.assertNotIn("--resume", command)
            self.assertEqual(api.posts[-1][1]["success"], True)
            run = root / "audio" / "lewis-carroll" / "alice-g11" / "audio" / "kokoro-af-heart" / "builds" / "1234567890abcdef" / "run.json"
            run.parent.mkdir(parents=True)
            run.write_text("{}")
            with patch.object(consumer.subprocess, "Popen", return_value=FakeChild()) as popen, patch.object(consumer.time, "sleep"):
                consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio", device="cuda")
            self.assertIn("--resume", popen.call_args.args[0])

    def test_api_rejects_non_tls_remote_endpoint(self):
        with self.assertRaises(ValueError):
            consumer.JobAPI("http://example.com/api/v1", "a" * 32)
        consumer.JobAPI("http://localhost:8787/api/v1", "a" * 32)

    def test_rejected_heartbeat_stops_pipeline_immediately(self):
        job = {"id": "job-1", "source_id": "gutenberg:11", "title": "Alice", "author": "Lewis Carroll",
               "epub_url": "https://www.gutenberg.org/ebooks/11.epub", "build_id": "1234567890abcdef", "lease_token": "lease"}
        class RejectedAPI(FakeAPI):
            def post(self, path, payload=None):
                if path.endswith("/heartbeat"):
                    raise urllib.error.HTTPError("https://example.com", 409, "Lease lost", {}, None)
                return super().post(path, payload)
        class RunningChild(FakeChild):
            def poll(self):
                return -15 if self.terminated else None
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            epub = root / "download" / "gutenberg" / "lewis-carroll" / "alice-g11.epub"
            epub.parent.mkdir(parents=True)
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
            child = RunningChild()
            with patch.object(consumer.subprocess, "Popen", return_value=child), \
                    patch.object(consumer.time, "sleep"), \
                    patch.object(consumer.time, "monotonic", side_effect=[0, 0, 31, 31, 31]):
                with self.assertRaisesRegex(RuntimeError, "lease rejected"):
                    consumer.process_job(RejectedAPI(), job, download_root=root / "download",
                                         audio_root=root / "audio", device="cuda")
            self.assertTrue(child.terminated)

    def test_books_cli_dispatches_consumer(self):
        from openshelf.pipeline import books
        with patch.object(consumer, "main", return_value=7) as run:
            self.assertEqual(books.main(["consume-jobs", "--api-base", "https://example.com/api/v1",
                                         "--once", "--device", "cuda"]), 7)
        run.assert_called_once_with(["--api-base", "https://example.com/api/v1", "--sync-pages", "0",
                                     "--device", "cuda", "--once"])


if __name__ == "__main__":
    unittest.main()
