"""Offline security and process-boundary tests for the PC job consumer."""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from openshelf.pipeline import job_consumer as consumer


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FakeAPI:
    base = "https://example.com/api/v1"
    token = "test-pc-token-longer-than-twenty-four"
    def __init__(self):
        self.posts = []

    def post(self, path, payload=None):
        self.posts.append((path, payload))
        return {"ok": True}

    def source_epub_cached(self, _source_id):
        return False


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
    def test_standard_ebooks_url_and_edition_isolation(self):
        from openshelf.pipeline import standard_ebooks_source as se
        source_id = "standardebooks:lewis-carroll/alice/john-tenniel"
        good = se.epub_url(source_id)
        self.assertEqual(consumer.validate_epub_url(good, source_id), good)
        for bad in (good.replace("standardebooks.org", "evil.example"),
                    good.replace("john-tenniel", "other"), good + "&evil=1",
                    good.replace("https:", "http:"), good + "#fragment"):
            with self.assertRaises(ValueError):
                consumer.validate_epub_url(bad, source_id)
        self.assertNotEqual(se.title_slug("Alice", source_id), se.title_slug("Alice", "standardebooks:lewis-carroll/alice"))
        self.assertEqual(se.title_slug("Alice", "gutenberg:11"), "alice-g11")

    def test_standard_ebooks_consumer_preserves_source_and_publication_slug(self):
        from openshelf.pipeline import standard_ebooks_source as se
        source_id = "standardebooks:lewis-carroll/alice/john-tenniel"
        job = {"id": "job-se", "source_id": source_id, "title": "Alice", "author": "Lewis Carroll",
               "epub_url": se.epub_url(source_id), "build_id": "1234567890abcdef", "lease_token": "lease"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            api = FakeAPI()
            with patch.object(consumer, "fetch_epub") as fetch, \
                    patch.object(consumer, "validate_local_epub"), \
                    patch.object(consumer, "check_spoken_text", return_value=100), \
                    patch.object(consumer.subprocess, "Popen", return_value=FakeChild()), \
                    patch.object(consumer.time, "sleep"):
                consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio", device="cuda")
            epub = fetch.call_args.args[2]
            self.assertIn("standard-ebooks", epub.parts)
            self.epub_rights.assert_called_once_with(epub, source_id)
            self.assertEqual(api.posts[-1][1]["title_slug"], se.title_slug("Alice", source_id))

    def setUp(self):
        metadata_patcher = patch("openshelf.pipeline.epub_parser.read_book_metadata", return_value={"language": "en"})
        metadata_patcher.start()
        self.addCleanup(metadata_patcher.stop)
        patcher = patch.object(consumer, "verify_public_domain")
        self.rights = patcher.start()
        self.addCleanup(patcher.stop)
        epub_patcher = patch.object(consumer, "check_epub_rights")
        self.epub_rights = epub_patcher.start()
        self.addCleanup(epub_patcher.stop)

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

    def test_epub_expanded_size_is_checked_before_decompression(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "book.epub"
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
                archive.writestr("large.txt", "x" * 1024)
            with patch.object(consumer, "MAX_EXPANDED_BYTES", 512):
                with self.assertRaisesRegex(ValueError, "expands beyond limits"):
                    consumer.validate_local_epub(target)

    def test_zip_directory_limit_precedes_zipfile_allocation(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "book.epub"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
                archive.writestr("chapter.xhtml", "<p>words</p>")
            with patch.object(consumer, "MAX_EPUB_ENTRIES", 1), \
                    patch.object(consumer.zipfile, "ZipFile") as parser:
                with self.assertRaisesRegex(ValueError, "directory exceeds limits"):
                    consumer.validate_local_epub(target)
                parser.assert_not_called()

    def test_empty_spoken_text_is_rejected_before_pipeline(self):
        job = {"id": "job-1", "source_id": "gutenberg:11", "title": "Alice", "author": "Lewis Carroll",
               "epub_url": "https://www.gutenberg.org/ebooks/11.epub", "build_id": "1234567890abcdef", "lease_token": "lease"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            epub = root / "download" / "gutenberg" / "lewis-carroll" / "alice-g11.epub"
            epub.parent.mkdir(parents=True)
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
            api = FakeAPI()
            with patch.object(consumer, "check_spoken_text", side_effect=ValueError("EPUB has no spoken words")), \
                    patch.object(consumer.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "no spoken words"):
                    consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio",
                                         device="cuda")
            popen.assert_not_called()
            self.rights.assert_called_once_with("gutenberg:11")
            self.epub_rights.assert_called_once_with(epub, "gutenberg:11")
            self.assertEqual(api.posts[-1][1]["error_code"], "ValueError")

    def test_spoken_text_accepts_large_books_and_counts_headings(self):
        parser = ModuleType("openshelf.pipeline.epub_parser")
        parser.parse_epub = lambda _path: [SimpleNamespace(
            word_count=999_999, heading=SimpleNamespace(spoken_text="Chapter One"))]
        with patch.dict(sys.modules, {"openshelf.pipeline.epub_parser": parser}):
            self.assertEqual(consumer.check_spoken_text(Path("unused.epub")), 1_000_001)

    def test_unlimited_word_budget_still_rejects_empty_text(self):
        parser = ModuleType("openshelf.pipeline.epub_parser")
        parser.parse_epub = lambda _path: []
        with patch.dict(sys.modules, {"openshelf.pipeline.epub_parser": parser}):
            with self.assertRaisesRegex(ValueError, "no spoken words"):
                consumer.check_spoken_text(Path("unused.epub"))

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
            with patch.object(consumer.subprocess, "Popen", return_value=child) as popen, \
                    patch.object(consumer, "check_spoken_text", return_value=150_000) as budget, patch.object(consumer.time, "sleep"):
                consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio", device="cuda")
            budget.assert_called_once_with(epub)
            command = popen.call_args.args[0]
            self.assertIn("--epub", command)
            self.assertIn(str(epub), command)
            self.assertIn("1234567890abcdef", command)
            self.assertNotIn("--resume", command)
            self.assertEqual(api.posts[-1][1]["success"], True)
            run = root / "audio" / "lewis-carroll" / "alice-g11" / "audio" / "kokoro-af-heart" / "builds" / "1234567890abcdef" / "run.json"
            run.parent.mkdir(parents=True)
            run.write_text("{}")
            with patch.object(consumer.subprocess, "Popen", return_value=FakeChild()) as popen, \
                    patch.object(consumer, "check_spoken_text", return_value=1000), patch.object(consumer.time, "sleep"):
                consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio", device="cuda")
            self.assertIn("--resume", popen.call_args.args[0])

    def test_expressive_job_uses_chatterbox_and_local_openai_direction(self):
        job = {"id": "job-2", "source_id": "gutenberg:11", "title": "Alice", "author": "Lewis Carroll",
               "epub_url": "https://www.gutenberg.org/ebooks/11.epub", "build_id": "1234567890abcdef",
               "lease_token": "lease", "mode": "expressive"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            epub = root / "download" / "gutenberg" / "lewis-carroll" / "alice-g11.epub"
            epub.parent.mkdir(parents=True)
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr("META-INF/container.xml", "<container/>")
            api = FakeAPI()
            with patch.object(consumer, "OPENAI_API_KEY", "test-local-key"), \
                    patch.object(consumer.subprocess, "Popen", return_value=FakeChild()) as popen, \
                    patch.object(consumer, "check_spoken_text", return_value=1_000_001) as budget, \
                    patch.object(consumer.time, "sleep"):
                consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio",
                                     device="cuda")
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--engine") + 1], "chatterbox")
            self.assertEqual(command[command.index("--voice") + 1], "chatterbox-af_heart")
            self.assertEqual(command[command.index("--rendition") + 1], "chatterbox-af-heart")
            self.assertEqual(command[command.index("--performance-direction") + 1], "batched")
            self.assertEqual(popen.call_args.kwargs["env"]["LLM_PROVIDER"], "openai")
            self.assertNotIn("test-local-key", str(api.posts))
            budget.assert_called_once_with(epub)

            with patch.object(consumer, "OPENAI_API_KEY", ""), \
                    patch.object(consumer, "check_spoken_text", return_value=1000), \
                    patch.object(consumer.subprocess, "Popen") as rejected:
                with self.assertRaisesRegex(ValueError, "OpenAI direction is not configured"):
                    consumer.process_job(api, job, download_root=root / "download", audio_root=root / "audio",
                                         device="cuda")
            rejected.assert_not_called()

    def test_api_rejects_non_tls_remote_endpoint(self):
        with self.assertRaises(ValueError):
            consumer.JobAPI("http://example.com/api/v1", "a" * 32)
        consumer.JobAPI("http://localhost:8787/api/v1", "a" * 32)

    def test_claim_only_advertises_expressive_with_a_local_key(self):
        class EmptyQueue(FakeAPI):
            def post(self, path, payload=None):
                super().post(path, payload)
                return {"job": None}
        for key, expected in [("", False), ("test-local-key", True)]:
            with self.subTest(configured=expected), patch.object(consumer, "OPENAI_API_KEY", key), \
                    patch.object(consumer, "JobAPI", return_value=EmptyQueue()) as connect, \
                    patch.object(consumer, "document_runtime_ready", return_value=True), \
                    patch.object(consumer, "english_runtime", return_value=None), \
                    patch.object(consumer, "mandarin_runtime", return_value=None):
                self.assertEqual(consumer.main(["--api-base", "https://example.com/api/v1", "--once"]), 0)
                request = connect.return_value.posts[0]
                self.assertEqual(request, ("/internal/generation-jobs/claim", {"expressive": expected, "mandarin": False, "documents": True, "english": True}))
                self.assertNotIn(key or "unconfigured", str(request))

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
                    patch.object(consumer, "check_spoken_text", return_value=1000), \
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

    def test_legacy_sync_page_limit_prevents_large_d1_import(self):
        with patch("sys.stderr", new_callable=io.StringIO) as errors:
            with self.assertRaises(SystemExit) as exit_status:
                consumer.main(["--api-base", "https://example.com/api/v1", "--sync-pages", "11"])
        self.assertEqual(exit_status.exception.code, 2)
        self.assertIn("--sync-pages must be 0..10", errors.getvalue())


class RightsTests(unittest.TestCase):
    def test_standard_ebooks_embedded_rights_require_exact_edition(self):
        source_id = "standardebooks:lewis-carroll/alice/john-tenniel"
        identity = "https://standardebooks.org/ebooks/lewis-carroll/alice/john-tenniel"
        rights = ("The source text and artwork in this ebook are believed to be in the United States public domain; "
                  "the creators dedicate their contributions to the worldwide public domain via "
                  "https://creativecommons.org/publicdomain/zero/1.0/.")
        with tempfile.TemporaryDirectory() as folder:
            epub = Path(folder) / "book.epub"
            def write(identifier, statement):
                with zipfile.ZipFile(epub, "w") as archive:
                    archive.writestr("content.opf", '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                                     f'<dc:identifier>{identifier}</dc:identifier><dc:rights>{statement}</dc:rights></package>')
            write(identity, rights)
            consumer.check_epub_rights(epub, source_id)
            for identifier, statement in ((identity.replace("/alice/", "/other/"), rights),
                                          (identity, "CC0"), (identity, "Public domain in the USA.")):
                write(identifier, statement)
                with self.assertRaises(consumer.RightsNotVerified):
                    consumer.check_epub_rights(epub, source_id)

    def test_embedded_epub_rights_and_copyright_banner(self):
        package = ('<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:rights>{}</dc:rights></package>')
        with tempfile.TemporaryDirectory() as folder:
            epub = Path(folder) / "book.epub"
            def write(rights, body):
                with zipfile.ZipFile(epub, "w") as archive:
                    archive.writestr("OEBPS/content.opf", package.format(rights))
                    archive.writestr("OEBPS/chapter.xhtml", body)
            write("Public domain in the USA.", "<p>Alice went down the rabbit hole.</p>")
            consumer.check_epub_rights(epub)
            write("Copyrighted. Read the copyright notice inside this book for details.", "<p>Text</p>")
            with self.assertRaises(consumer.RightsNotVerified):
                consumer.check_epub_rights(epub)
            write("Public domain in the USA.",
                  "<p>*** This is a COPYRIGHTED Project Gutenberg eBook. Details Below. ***</p>")
            with self.assertRaises(consumer.RightsNotVerified):
                consumer.check_epub_rights(epub)

    def test_official_rdf_must_explicitly_say_public_domain(self):
        def rdf(rights):
            return (f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                    f'xmlns:dcterms="http://purl.org/dc/terms/" '
                    f'xmlns:pgterms="http://www.gutenberg.org/2009/pgterms/">'
                    f'<pgterms:ebook rdf:about="ebooks/11"><dcterms:rights>{rights}</dcterms:rights>'
                    f'</pgterms:ebook><pgterms:file '
                    f'rdf:about="https://www.gutenberg.org/ebooks/11.epub.images">'
                    f'<rdf:value>application/epub+zip</rdf:value></pgterms:file></rdf:RDF>').encode()
        for rights, allowed in [("Public domain in the USA.", True), ("Copyrighted.", False),
                                ("Rights unknown", False)]:
            with self.subTest(rights=rights), patch.object(consumer.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = FakeResponse(rdf(rights))
                if allowed:
                    consumer.verify_public_domain("gutenberg:11")
                else:
                    with self.assertRaises(consumer.RightsNotVerified):
                        consumer.verify_public_domain("gutenberg:11")
                self.assertIn("/cache/epub/11/pg11.rdf", opener.return_value.open.call_args.args[0].full_url)

    def test_malformed_or_oversized_rights_fail_closed(self):
        for payload in (b"<invalid", b"<!DOCTYPE x><rights>Public domain in the USA.</rights>",
                        b"x" * (consumer.MAX_RIGHTS_BYTES + 1)):
            with self.subTest(size=len(payload)), patch.object(consumer.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = FakeResponse(payload)
                with self.assertRaises(consumer.RightsNotVerified):
                    consumer.verify_public_domain("gutenberg:11")

    def test_gutendex_sync_skips_copyrighted_or_unknown_books(self):
        def record(number, copyright):
            return {"id": number, "copyright": copyright, "media_type": "Text",
                    "languages": ["en"], "title": f"Book {number}", "authors": [{"name": "Writer"}],
                    "formats": {"application/epub+zip":
                                f"https://www.gutenberg.org/ebooks/{number}.epub.images"}}
        page = {"results": [record(11, False), record(12, True), record(13, None)], "next": None}
        def cache_book(api, book):
            api.post("/internal/source-books/sync", {"books": [book]})
        with patch.object(consumer.urllib.request, "urlopen", return_value=FakeResponse(json.dumps(page).encode())), \
                patch("openshelf.pipeline.source_catalog.cache_book", side_effect=cache_book), \
                patch.object(consumer, "verify_public_domain", return_value={"rights_statement": "Public domain in the USA."}):
            api = FakeAPI()
            self.assertEqual(consumer.sync_gutenberg(api, 1), 1)
        self.assertEqual([item["source_id"] for item in api.posts[0][1]["books"]], ["gutenberg:11"])


if __name__ == "__main__":
    unittest.main()
