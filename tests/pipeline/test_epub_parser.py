"""Tests for epub_parser — Step 1 of the pipeline."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.epub_parser import parse_epub, Chapter


# --- Test helpers ---

def _make_item(filename: str, html: str):
    """Create a mock EpubItem with given filename and HTML content."""
    item = MagicMock()
    item.get_name.return_value = filename
    item.get_content.return_value = html.encode("utf-8")
    return item


def _make_book(items: list):
    """Create a mock EpubBook that returns given items as ITEM_DOCUMENT."""
    book = MagicMock()
    book.get_items_of_type.return_value = items
    return book


def _words_html(n: int, base: str = "word") -> str:
    """Generate HTML with n words in a <p> tag."""
    words = " ".join(f"{base}{i}" for i in range(n))
    return f"<html><body><p>{words}</p></body></html>"


def _chapter_html(title_tag: str, title: str, word_count: int = 60) -> str:
    """Generate HTML with a heading and paragraph."""
    words = " ".join(f"word{i}" for i in range(word_count))
    return f"<html><body><{title_tag}>{title}</{title_tag}><p>{words}</p></body></html>"


# --- Tests ---

class TestParseEpubBasic(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_single_chapter(self, mock_read):
        html = _chapter_html("h2", "My Chapter", 60)
        mock_read.return_value = _make_book([_make_item("ch01.xhtml", html)])

        chapters = parse_epub("fake.epub")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].number, 1)
        self.assertEqual(chapters[0].title, "My Chapter")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_multiple_chapters_numbered(self, mock_read):
        items = [
            _make_item(f"ch{i}.xhtml", _chapter_html("h2", f"Ch {i}", 60))
            for i in range(1, 4)
        ]
        mock_read.return_value = _make_book(items)

        chapters = parse_epub("fake.epub")
        self.assertEqual(len(chapters), 3)
        self.assertEqual([c.number for c in chapters], [1, 2, 3])

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_word_count_correct(self, mock_read):
        html = _chapter_html("h2", "Title", 75)
        mock_read.return_value = _make_book([_make_item("ch01.xhtml", html)])

        chapters = parse_epub("fake.epub")
        self.assertEqual(chapters[0].word_count, 75)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_paragraph_separation(self, mock_read):
        html = "<html><body><h1>T</h1><p>A.</p><p>B.</p></body></html>"
        # Need enough words total
        words_a = " ".join(f"a{i}" for i in range(30))
        words_b = " ".join(f"b{i}" for i in range(30))
        html = f"<html><body><h1>T</h1><p>{words_a}</p><p>{words_b}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])

        chapters = parse_epub("fake.epub")
        self.assertIn("\n\n", chapters[0].text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_whitespace_normalization(self, mock_read):
        words = "  lots   of   spaces  "
        all_words = " ".join(f"w{i}" for i in range(55))
        html = f"<html><body><h1>T</h1><p>{words} {all_words}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])

        chapters = parse_epub("fake.epub")
        self.assertNotIn("  ", chapters[0].text)


class TestParseEpubTitleExtraction(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_title_from_h1(self, mock_read):
        html = _chapter_html("h1", "Heading One", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(parse_epub("f.epub")[0].title, "Heading One")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_title_from_h2_when_no_h1(self, mock_read):
        html = _chapter_html("h2", "Heading Two", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(parse_epub("f.epub")[0].title, "Heading Two")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_title_from_h3_fallback(self, mock_read):
        html = _chapter_html("h3", "Heading Three", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(parse_epub("f.epub")[0].title, "Heading Three")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_fallback_chapter_n(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><p>{words}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(parse_epub("f.epub")[0].title, "Chapter 1")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_h1_preferred_over_h2(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><h2>Second</h2><h1>First</h1><p>{words}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(parse_epub("f.epub")[0].title, "First")


class TestParseEpubFiltering(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_skips_nav(self, mock_read):
        items = [
            _make_item("nav.xhtml", _words_html(60)),
            _make_item("ch01.xhtml", _chapter_html("h2", "Ch", 60)),
        ]
        mock_read.return_value = _make_book(items)
        self.assertEqual(len(parse_epub("f.epub")), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_skips_toc(self, mock_read):
        items = [
            _make_item("toc.xhtml", _words_html(60)),
            _make_item("ch01.xhtml", _chapter_html("h2", "Ch", 60)),
        ]
        mock_read.return_value = _make_book(items)
        self.assertEqual(len(parse_epub("f.epub")), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_skips_cover(self, mock_read):
        items = [
            _make_item("cover.xhtml", _words_html(60)),
            _make_item("ch01.xhtml", _chapter_html("h2", "Ch", 60)),
        ]
        mock_read.return_value = _make_book(items)
        self.assertEqual(len(parse_epub("f.epub")), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_titlepage_not_skipped(self, mock_read):
        items = [
            _make_item("titlepage.xhtml", _chapter_html("h1", "Title Page", 60)),
            _make_item("ch01.xhtml", _chapter_html("h2", "Ch", 60)),
        ]
        mock_read.return_value = _make_book(items)
        chapters = parse_epub("f.epub")
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "Title Page")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_skip_is_case_insensitive_substring(self, mock_read):
        items = [
            _make_item("EPUB/Navigation.xhtml", _words_html(60)),
            _make_item("ch01.xhtml", _chapter_html("h2", "Ch", 60)),
        ]
        mock_read.return_value = _make_book(items)
        self.assertEqual(len(parse_epub("f.epub")), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_skips_under_50_words(self, mock_read):
        html = _chapter_html("h2", "Short", 30)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(len(parse_epub("f.epub")), 0)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_keeps_exactly_50_words(self, mock_read):
        html = _chapter_html("h2", "Exact", 50)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(len(parse_epub("f.epub")), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_numbering_skips_filtered(self, mock_read):
        items = [
            _make_item("nav.xhtml", _words_html(60)),
            _make_item("ch-a.xhtml", _chapter_html("h2", "A", 60)),
            _make_item("ch-short.xhtml", _chapter_html("h2", "Short", 20)),
            _make_item("ch-b.xhtml", _chapter_html("h2", "B", 60)),
        ]
        mock_read.return_value = _make_book(items)
        chapters = parse_epub("f.epub")
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].number, 1)
        self.assertEqual(chapters[1].number, 2)


class TestParseEpubHtmlCleaning(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_sup_tags_removed(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><h1>T</h1><p>{words}<sup>1</sup></p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        self.assertNotIn("1", chapters[0].text.split()[-1:])

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_sub_tags_removed(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><h1>T</h1><p>{words}<sub>2</sub></p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        self.assertNotIn("<sub>", chapters[0].text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_numeric_anchor_tags_removed(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f'<html><body><h1>T</h1><p>{words}<a href="#note1">1</a></p></body></html>'
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        # The "1" from the anchor should be removed
        self.assertEqual(chapters[0].word_count, 60)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_non_numeric_anchor_tags_kept(self, mock_read):
        words = " ".join(f"w{i}" for i in range(55))
        html = f'<html><body><h1>T</h1><p>{words} <a href="url">click here</a></p></body></html>'
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        self.assertIn("click here", chapters[0].text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_nested_html_preserved(self, mock_read):
        words = " ".join(f"w{i}" for i in range(55))
        html = f"<html><body><h1>T</h1><p><em>italic</em> and <strong>bold</strong> {words}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        self.assertIn("italic", chapters[0].text)
        self.assertIn("bold", chapters[0].text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_malformed_html_no_crash(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><h1>T<p>{words}</p></body>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        # Should not raise
        parse_epub("f.epub")


class TestParseEpubEdgeCases(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_empty_epub(self, mock_read):
        mock_read.return_value = _make_book([])
        self.assertEqual(parse_epub("f.epub"), [])

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_whitespace_only_item(self, mock_read):
        html = "<html><body><p>   </p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(len(parse_epub("f.epub")), 0)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_unicode_content(self, mock_read):
        words = " ".join(f"w{i}" for i in range(55))
        html = f'<html><body><h1>T</h1><p>em\u2014dash and \u201ccurly\u201d {words}</p></body></html>'
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        self.assertIn("\u2014", chapters[0].text)
        self.assertIn("\u201c", chapters[0].text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_no_p_tags(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><h1>T</h1><div>{words}</div></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        # Fallback to soup.get_text() should extract the div content
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].word_count, 60)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_image_only_item(self, mock_read):
        html = '<html><body><img src="pic.jpg"/></body></html>'
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(len(parse_epub("f.epub")), 0)


if __name__ == "__main__":
    unittest.main()
