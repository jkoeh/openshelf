"""Unit tests for openshelf.scrapers — all network calls are mocked."""

import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline import books
from openshelf.scrapers import http, gutenberg, standard_ebooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(data):
    """Return a context-manager mock that behaves like urlopen's response."""
    resp = MagicMock()
    resp.read.return_value = data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# sanitize()
# ---------------------------------------------------------------------------

class TestSanitize(unittest.TestCase):
    def test_normal_name(self):
        self.assertEqual(http.sanitize("Fyodor Dostoyevsky"), "fyodor-dostoyevsky")

    def test_special_characters(self):
        self.assertEqual(http.sanitize("Crime & Punishment!"), "crime-punishment")

    def test_already_clean(self):
        self.assertEqual(http.sanitize("hello"), "hello")

    def test_empty_string(self):
        self.assertEqual(http.sanitize(""), "")

    def test_whitespace_only(self):
        self.assertEqual(http.sanitize("   "), "")

    def test_leading_trailing_specials(self):
        self.assertEqual(http.sanitize("--hello--"), "hello")


class TestSearchMatching(unittest.TestCase):
    def test_matches_apostrophe_insensitive_title(self):
        self.assertTrue(
            http.matches_filter(
                "Alice's Adventures in Wonderland",
                "Alices Adventures In Wonderland",
            )
        )

    def test_matches_author_tokens_in_different_order(self):
        self.assertTrue(http.matches_filter("Lewis Carroll", "Carroll, Lewis"))

    def test_excludes_missing_token(self):
        self.assertFalse(http.matches_filter("Alice Looking Glass", "Alices Adventures"))


# ---------------------------------------------------------------------------
# make_request()
# ---------------------------------------------------------------------------

class TestMakeRequest(unittest.TestCase):
    @patch.object(http._opener, "open")
    def test_successful_fetch(self, mock_open):
        mock_open.return_value = _mock_response(b"hello")
        result = http.make_request("http://example.com")
        self.assertEqual(result, b"hello")

    @patch.object(http._opener, "open")
    def test_url_error_returns_none(self, mock_open):
        import urllib.error
        mock_open.side_effect = urllib.error.URLError("fail")
        result = http.make_request("http://example.com")
        self.assertIsNone(result)

    @patch.object(http._opener, "open")
    def test_custom_accept_header(self, mock_open):
        mock_open.return_value = _mock_response(b"ok")
        http.make_request("http://example.com", accept="application/json")
        req_obj = mock_open.call_args[0][0]
        self.assertEqual(req_obj.get_header("Accept"), "application/json")


# ---------------------------------------------------------------------------
# gutenberg_search()
# ---------------------------------------------------------------------------

class TestGutenbergSearch(unittest.TestCase):
    def _api_page(self, results, next_url=None):
        return json.dumps({"results": results, "next": next_url}).encode()

    def _book(self, title="A Book", author="Author Name", epub_url="http://x.epub"):
        entry = {
            "title": title,
            "authors": [{"name": author}] if author is not None else [],
            "formats": {},
        }
        if epub_url:
            entry["formats"]["application/epub+zip"] = epub_url
        return entry

    @patch.object(gutenberg, "make_request")
    def test_single_page(self, mock_req):
        mock_req.return_value = self._api_page([self._book()])
        results = list(gutenberg.gutenberg_search(delay=0))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], ("Author Name", "A Book", "http://x.epub"))

    @patch.object(gutenberg, "make_request")
    def test_no_epub_skipped(self, mock_req):
        mock_req.return_value = self._api_page([self._book(epub_url=None)])
        results = list(gutenberg.gutenberg_search(delay=0))
        self.assertEqual(results, [])

    @patch.object(gutenberg, "make_request")
    def test_no_authors_defaults_unknown(self, mock_req):
        mock_req.return_value = self._api_page([self._book(author=None)])
        results = list(gutenberg.gutenberg_search(delay=0))
        self.assertEqual(results[0][0], "unknown")

    @patch.object(gutenberg, "make_request")
    def test_pagination(self, mock_req):
        page1 = self._api_page(
            [self._book(title="Book 1")],
            next_url="http://gutendex.com/books?page=2",
        )
        page2 = self._api_page([self._book(title="Book 2")])
        mock_req.side_effect = [page1, page2]
        results = list(gutenberg.gutenberg_search(delay=0))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][1], "Book 1")
        self.assertEqual(results[1][1], "Book 2")
        self.assertEqual(mock_req.call_count, 2)

    @patch.object(gutenberg, "make_request")
    def test_network_error_stops(self, mock_req):
        mock_req.return_value = None
        results = list(gutenberg.gutenberg_search(delay=0))
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# se_search()
# ---------------------------------------------------------------------------

class TestSeSearch(unittest.TestCase):
    @patch.object(standard_ebooks, "make_request")
    def test_parses_about_attributes(self, mock_req):
        html = '<li about="/ebooks/fyodor-dostoevsky/crime-and-punishment/constance-garnett">'
        mock_req.return_value = html.encode()
        results = list(standard_ebooks.se_search(delay=0))
        self.assertEqual(len(results), 1)
        author, title, url = results[0]
        self.assertEqual(author, "Fyodor Dostoevsky")
        self.assertEqual(title, "Crime And Punishment")
        self.assertIn(".epub", url)

    @patch.object(standard_ebooks, "make_request")
    def test_download_url_uses_hyphens_for_translators(self, mock_req):
        html = '<li about="/ebooks/franz-kafka/the-metamorphosis/willa-muir_edwin-muir">'
        mock_req.return_value = html.encode()
        results = list(standard_ebooks.se_search(delay=0))
        _, _, url = results[0]
        # Translators separated by _ in path become - in filename
        expected = (
            "https://standardebooks.org/ebooks/franz-kafka/the-metamorphosis"
            "/willa-muir_edwin-muir/downloads"
            "/franz-kafka_the-metamorphosis_willa-muir-edwin-muir.epub"
        )
        self.assertEqual(url, expected)

    @patch.object(standard_ebooks, "make_request")
    def test_skips_not_public_domain_placeholders(self, mock_req):
        html = (
            '<li typeof="schema:Book" '
            'about="/ebooks/franz-kafka/the-metamorphosis/willa-muir_edwin-muir" '
            'class="ribbon not-pd">'
        )
        mock_req.return_value = html.encode()
        results = list(standard_ebooks.se_search(author="Franz Kafka", delay=0))
        self.assertEqual(results, [])

    @patch.object(standard_ebooks, "make_request")
    def test_no_ebooks_matched(self, mock_req):
        mock_req.return_value = b"<p>No ebooks matched your filter.</p>"
        results = list(standard_ebooks.se_search(delay=0))
        self.assertEqual(results, [])

    @patch.object(standard_ebooks, "make_request")
    def test_author_filter_case_insensitive(self, mock_req):
        html = '<li about="/ebooks/leo-tolstoy/war-and-peace/louise-maude">'
        mock_req.return_value = html.encode()
        results = list(standard_ebooks.se_search(author="tolstoy", delay=0))
        self.assertEqual(len(results), 1)
        self.assertIn("Tolstoy", results[0][0])

    @patch.object(standard_ebooks, "make_request")
    def test_author_filter_excludes_non_matching(self, mock_req):
        html = '<li about="/ebooks/leo-tolstoy/war-and-peace/louise-maude">'
        mock_req.return_value = html.encode()
        results = list(standard_ebooks.se_search(author="dostoevsky", delay=0))
        self.assertEqual(results, [])

    @patch.object(standard_ebooks, "make_request")
    def test_author_filter_handles_inverted_names(self, mock_req):
        html = '<li about="/ebooks/carroll-lewis/alices-adventures-in-wonderland">'
        mock_req.return_value = html.encode()
        results = list(standard_ebooks.se_search(author="Lewis Carroll", delay=0))
        self.assertEqual(len(results), 1)

    @patch.object(standard_ebooks, "make_request")
    def test_pagination(self, mock_req):
        page1 = '<li about="/ebooks/author-one/book-one/translator">page=2'
        page2 = '<li about="/ebooks/author-two/book-two/translator">'
        mock_req.side_effect = [page1.encode(), page2.encode()]
        results = list(standard_ebooks.se_search(delay=0))
        self.assertEqual(len(results), 2)

    @patch.object(standard_ebooks, "make_request")
    def test_network_error_stops(self, mock_req):
        mock_req.return_value = None
        results = list(standard_ebooks.se_search(delay=0))
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# download_book()
# ---------------------------------------------------------------------------

class TestDownloadBook(unittest.TestCase):
    def test_dry_run(self):
        result = http.download_book(
            "Author", "Title", "http://x.epub", "/tmp/fake", dry_run=True
        )
        self.assertTrue(result)

    def test_file_already_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            author_dir = os.path.join(tmpdir, "author")
            os.makedirs(author_dir)
            filepath = os.path.join(author_dir, "title.epub")
            with open(filepath, "wb") as f:
                f.write(b"existing")
            result = http.download_book("Author", "Title", "http://x.epub", tmpdir)
            self.assertTrue(result)
            with open(filepath, "rb") as f:
                self.assertEqual(f.read(), b"existing")

    @patch.object(http, "make_request")
    def test_new_download(self, mock_req):
        epub_data = b"PK\x03\x04epub-data"
        mock_req.return_value = epub_data
        with tempfile.TemporaryDirectory() as tmpdir:
            result = http.download_book(
                "Author", "Title", "http://x.epub", tmpdir, delay=0
            )
            self.assertTrue(result)
            filepath = os.path.join(tmpdir, "author", "title.epub")
            self.assertTrue(os.path.exists(filepath))
            with open(filepath, "rb") as f:
                self.assertEqual(f.read(), epub_data)

    @patch.object(http, "make_request")
    def test_failed_download(self, mock_req):
        mock_req.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            result = http.download_book(
                "Author", "Title", "http://x.epub", tmpdir, delay=0
            )
            self.assertFalse(result)

    @patch.object(http, "make_request")
    def test_se_html_response_retries(self, mock_req):
        html_page = b'<html>standardebooks.org download page</html>'
        epub_data = b"PK\x03\x04real-epub"
        mock_req.side_effect = [html_page, epub_data]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = http.download_book(
                "Author", "Title", "http://x.epub", tmpdir, delay=0
            )
            self.assertTrue(result)
            filepath = os.path.join(tmpdir, "author", "title.epub")
            with open(filepath, "rb") as f:
                self.assertEqual(f.read(), epub_data)
            self.assertEqual(mock_req.call_count, 2)

    @patch.object(http, "make_request")
    def test_se_html_response_retry_fails(self, mock_req):
        html_page = b'<html>standardebooks.org download page</html>'
        mock_req.return_value = html_page
        with tempfile.TemporaryDirectory() as tmpdir:
            result = http.download_book(
                "Author", "Title", "http://x.epub", tmpdir, delay=0
            )
            self.assertFalse(result)

    @patch.object(http, "make_request")
    def test_filename_sanitization(self, mock_req):
        mock_req.return_value = b"PK\x03\x04data"
        with tempfile.TemporaryDirectory() as tmpdir:
            http.download_book(
                "Fyodor Dostoyevsky", "Crime & Punishment!",
                "http://x.epub", tmpdir, delay=0,
            )
            expected = os.path.join(
                tmpdir, "fyodor-dostoyevsky", "crime-punishment.epub"
            )
            self.assertTrue(os.path.exists(expected))


class TestProcessBooksFiltering(unittest.TestCase):
    def test_standard_ebooks_book_filter_ignores_apostrophe(self):
        def fake_download(author_name, title, epub_url, source_dir, delay=0):
            author_slug = http.sanitize(author_name)
            title_slug = http.sanitize(title) or "untitled"
            author_dir = os.path.join(source_dir, author_slug)
            os.makedirs(author_dir, exist_ok=True)
            with open(os.path.join(author_dir, title_slug + ".epub"), "wb") as f:
                f.write(b"PK\x03\x04")
            return True

        args = types.SimpleNamespace(
            source="standard-ebooks",
            download_dir=tempfile.mkdtemp(),
            author="Lewis Carroll",
            book="Alice's Adventures in Wonderland",
            delay=0,
        )
        with (
            patch.object(books, "se_search", return_value=[
                ("Lewis Carroll", "Alices Adventures In Wonderland", "http://x.epub"),
            ]),
            patch.object(books, "download_book", side_effect=fake_download),
        ):
            downloaded = books.search_and_download(args)

        self.assertEqual(len(downloaded), 1)

    def test_convert_book_forwards_performance_direction(self):
        args = types.SimpleNamespace(
            output="audio",
            engine="chatterbox",
            voice=None,
            rendition=None,
            cast_mode=None,
            performance_direction="batched",
            device="cuda",
            chapters=None,
            keep_wav=False,
            upload=False,
            dry_run=False,
            build_id=None,
            resume=False,
            force=False,
            log_dir="logs",
        )

        with patch.object(
            books.subprocess,
            "run",
            return_value=types.SimpleNamespace(returncode=0),
        ) as run:
            rc = books.convert_book("book.epub", "local", args)

        self.assertEqual(rc, 0)
        cmd = run.call_args[0][0]
        self.assertIn("-m", cmd)
        self.assertIn("openshelf.pipeline.dag.cli", cmd)
        self.assertIn("run", cmd)
        self.assertNotIn("pipeline/scripts", " ".join(cmd))
        self.assertIn("--performance-direction", cmd)
        self.assertIn("batched", cmd)
        self.assertIn("PYTHONPATH", run.call_args.kwargs["env"])

    def test_main_exits_nonzero_when_no_books_downloaded(self):
        with (
            patch.object(books, "configure_console_output"),
            patch.object(books, "configure_pipeline_logging", return_value="test.log"),
            patch.object(books, "search_and_download", return_value=[]),
        ):
            code = books.main(["process", "--author", "No Such Author", "--skip-preflight"])

        self.assertEqual(code, 1)

    def test_main_exits_nonzero_when_convert_fails(self):
        with (
            patch.object(books, "configure_console_output"),
            patch.object(books, "configure_pipeline_logging", return_value="test.log"),
            patch.object(books, "convert_book", return_value=1),
        ):
            code = books.main(["process", "--epub", __file__, "--skip-preflight"])

        self.assertEqual(code, 1)

    def test_main_refreshes_catalog_after_successful_upload(self):
        with (
            patch.object(books, "configure_console_output"),
            patch.object(books, "configure_pipeline_logging", return_value="test.log"),
            patch.object(books, "convert_book", return_value=0),
            patch.object(books, "refresh_catalog", return_value=0) as refresh,
        ):
            code = books.main(["process", "--epub", __file__, "--upload", "--skip-preflight"])

        self.assertEqual(code, 0)
        refresh.assert_called_once()

    def test_main_skips_catalog_refresh_without_upload(self):
        with (
            patch.object(books, "configure_console_output"),
            patch.object(books, "configure_pipeline_logging", return_value="test.log"),
            patch.object(books, "convert_book", return_value=0),
            patch.object(books, "refresh_catalog") as refresh,
        ):
            code = books.main(["process", "--epub", __file__, "--skip-preflight"])

        self.assertEqual(code, 0)
        refresh.assert_not_called()

    def test_main_exits_nonzero_when_catalog_refresh_fails(self):
        with (
            patch.object(books, "configure_console_output"),
            patch.object(books, "configure_pipeline_logging", return_value="test.log"),
            patch.object(books, "convert_book", return_value=0),
            patch.object(books, "refresh_catalog", return_value=2),
        ):
            code = books.main(["process", "--epub", __file__, "--upload", "--skip-preflight"])

        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
