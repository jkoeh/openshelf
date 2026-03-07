"""Unit tests for download-books.py — all network calls are mocked."""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Import module despite hyphenated filename
_spec = importlib.util.spec_from_file_location(
    "download_books",
    os.path.join(os.path.dirname(__file__), "download-books.py"),
)
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)


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
        self.assertEqual(db.sanitize("Fyodor Dostoyevsky"), "fyodor-dostoyevsky")

    def test_special_characters(self):
        self.assertEqual(db.sanitize("Crime & Punishment!"), "crime-punishment")

    def test_already_clean(self):
        self.assertEqual(db.sanitize("hello"), "hello")

    def test_empty_string(self):
        self.assertEqual(db.sanitize(""), "")

    def test_whitespace_only(self):
        self.assertEqual(db.sanitize("   "), "")

    def test_leading_trailing_specials(self):
        self.assertEqual(db.sanitize("--hello--"), "hello")


# ---------------------------------------------------------------------------
# make_request()
# ---------------------------------------------------------------------------

class TestMakeRequest(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_successful_fetch(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(b"hello")
        result = db.make_request("http://example.com")
        self.assertEqual(result, b"hello")

    @patch("urllib.request.urlopen")
    def test_url_error_returns_none(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("fail")
        result = db.make_request("http://example.com")
        self.assertIsNone(result)

    @patch("urllib.request.urlopen")
    def test_custom_accept_header(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(b"ok")
        db.make_request("http://example.com", accept="application/json")
        req_obj = mock_urlopen.call_args[0][0]
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

    @patch.object(db, "make_request")
    def test_single_page(self, mock_req):
        mock_req.return_value = self._api_page([self._book()])
        results = list(db.gutenberg_search(delay=0))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], ("Author Name", "A Book", "http://x.epub"))

    @patch.object(db, "make_request")
    def test_no_epub_skipped(self, mock_req):
        mock_req.return_value = self._api_page([self._book(epub_url=None)])
        results = list(db.gutenberg_search(delay=0))
        self.assertEqual(results, [])

    @patch.object(db, "make_request")
    def test_no_authors_defaults_unknown(self, mock_req):
        mock_req.return_value = self._api_page([self._book(author=None)])
        results = list(db.gutenberg_search(delay=0))
        self.assertEqual(results[0][0], "unknown")

    @patch.object(db, "make_request")
    def test_pagination(self, mock_req):
        page1 = self._api_page(
            [self._book(title="Book 1")],
            next_url="http://gutendex.com/books?page=2",
        )
        page2 = self._api_page([self._book(title="Book 2")])
        mock_req.side_effect = [page1, page2]
        results = list(db.gutenberg_search(delay=0))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][1], "Book 1")
        self.assertEqual(results[1][1], "Book 2")
        self.assertEqual(mock_req.call_count, 2)

    @patch.object(db, "make_request")
    def test_network_error_stops(self, mock_req):
        mock_req.return_value = None
        results = list(db.gutenberg_search(delay=0))
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# se_search()
# ---------------------------------------------------------------------------

class TestSeSearch(unittest.TestCase):
    @patch.object(db, "make_request")
    def test_parses_about_attributes(self, mock_req):
        html = '<li about="/ebooks/fyodor-dostoevsky/crime-and-punishment/constance-garnett">'
        mock_req.return_value = html.encode()
        results = list(db.se_search(delay=0))
        self.assertEqual(len(results), 1)
        author, title, url = results[0]
        self.assertEqual(author, "Fyodor Dostoevsky")
        self.assertEqual(title, "Crime And Punishment")
        self.assertIn(".epub", url)

    @patch.object(db, "make_request")
    def test_no_ebooks_matched(self, mock_req):
        mock_req.return_value = b"<p>No ebooks matched your filter.</p>"
        results = list(db.se_search(delay=0))
        self.assertEqual(results, [])

    @patch.object(db, "make_request")
    def test_author_filter_case_insensitive(self, mock_req):
        html = '<li about="/ebooks/leo-tolstoy/war-and-peace/louise-maude">'
        mock_req.return_value = html.encode()
        results = list(db.se_search(author="tolstoy", delay=0))
        self.assertEqual(len(results), 1)
        self.assertIn("Tolstoy", results[0][0])

    @patch.object(db, "make_request")
    def test_author_filter_excludes_non_matching(self, mock_req):
        html = '<li about="/ebooks/leo-tolstoy/war-and-peace/louise-maude">'
        mock_req.return_value = html.encode()
        results = list(db.se_search(author="dostoevsky", delay=0))
        self.assertEqual(results, [])

    @patch.object(db, "make_request")
    def test_pagination(self, mock_req):
        page1 = '<li about="/ebooks/author-one/book-one/translator">page=2'
        page2 = '<li about="/ebooks/author-two/book-two/translator">'
        mock_req.side_effect = [page1.encode(), page2.encode()]
        results = list(db.se_search(delay=0))
        self.assertEqual(len(results), 2)

    @patch.object(db, "make_request")
    def test_network_error_stops(self, mock_req):
        mock_req.return_value = None
        results = list(db.se_search(delay=0))
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# download_book()
# ---------------------------------------------------------------------------

class TestDownloadBook(unittest.TestCase):
    def test_dry_run(self):
        result = db.download_book(
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
            result = db.download_book("Author", "Title", "http://x.epub", tmpdir)
            self.assertTrue(result)
            # File content unchanged
            with open(filepath, "rb") as f:
                self.assertEqual(f.read(), b"existing")

    @patch.object(db, "make_request")
    def test_new_download(self, mock_req):
        mock_req.return_value = b"epub-data"
        with tempfile.TemporaryDirectory() as tmpdir:
            result = db.download_book(
                "Author", "Title", "http://x.epub", tmpdir, delay=0
            )
            self.assertTrue(result)
            filepath = os.path.join(tmpdir, "author", "title.epub")
            self.assertTrue(os.path.exists(filepath))
            with open(filepath, "rb") as f:
                self.assertEqual(f.read(), b"epub-data")

    @patch.object(db, "make_request")
    def test_failed_download(self, mock_req):
        mock_req.return_value = None
        with tempfile.TemporaryDirectory() as tmpdir:
            result = db.download_book(
                "Author", "Title", "http://x.epub", tmpdir, delay=0
            )
            self.assertFalse(result)

    @patch.object(db, "make_request")
    def test_filename_sanitization(self, mock_req):
        mock_req.return_value = b"data"
        with tempfile.TemporaryDirectory() as tmpdir:
            db.download_book(
                "Fyodor Dostoyevsky", "Crime & Punishment!",
                "http://x.epub", tmpdir, delay=0,
            )
            expected = os.path.join(
                tmpdir, "fyodor-dostoyevsky", "crime-punishment.epub"
            )
            self.assertTrue(os.path.exists(expected))


if __name__ == "__main__":
    unittest.main()
