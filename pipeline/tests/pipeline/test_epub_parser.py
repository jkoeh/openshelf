"""Tests for epub_parser — Step 1 of the pipeline."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.epub_parser import (
    parse_epub,
    Chapter,
    ContentElement,
    normalize_heading_for_tts,
)


# --- Test helpers ---

def _make_item(filename: str, html: str, item_id: str | None = None):
    """Create a mock EpubItem with given filename and HTML content."""
    item = MagicMock()
    item.get_name.return_value = filename
    item.get_id.return_value = item_id or filename.rsplit("/", 1)[-1]
    item.get_content.return_value = html.encode("utf-8")
    return item


def _make_book(items: list):
    """Create a mock EpubBook that returns given items as ITEM_DOCUMENT."""
    book = MagicMock()
    book.get_items_of_type.return_value = items
    return book


def _make_book_with_spine(items: list, spine: list[str]):
    book = _make_book(items)
    book.spine = [(item_id, "yes") for item_id in spine]
    by_id = {item.get_id(): item for item in items}
    book.get_item_with_id.side_effect = lambda item_id: by_id.get(item_id)
    return book


def _words_html(n: int, base: str = "word") -> str:
    """Generate HTML with n words in a <p> tag (no heading)."""
    words = " ".join(f"{base}{i}" for i in range(n))
    return f"<html><body><p>{words}</p></body></html>"


def _chapter_html(title_tag: str, title: str, word_count: int = 60) -> str:
    """Generate HTML with a heading and paragraph.

    Note: the heading is now a spoken content element, so word_count refers
    only to the <p> words; total spoken words = word_count + len(title.split()).
    """
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
    def test_uses_spine_order_when_available(self, mock_read):
        items = [
            _make_item("text/chapter-1.xhtml", _chapter_html("h2", "Chapter One", 60), item_id="chapter-1.xhtml"),
            _make_item("text/epigraph.xhtml", _chapter_html("h2", "Epigraph", 60), item_id="epigraph.xhtml"),
        ]
        mock_read.return_value = _make_book_with_spine(items, ["epigraph.xhtml", "chapter-1.xhtml"])

        chapters = parse_epub("fake.epub")

        self.assertEqual([ch.title for ch in chapters], ["Epigraph", "Chapter One"])
        self.assertEqual([ch.number for ch in chapters], [1, 2])

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_word_count_includes_heading(self, mock_read):
        # heading "Title" = 1 word, paragraph has 75 words → total 76
        html = _chapter_html("h2", "Title", 75)
        mock_read.return_value = _make_book([_make_item("ch01.xhtml", html)])

        chapters = parse_epub("fake.epub")
        self.assertEqual(chapters[0].word_count, 76)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_paragraph_separation(self, mock_read):
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

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_epub_item_name_stored(self, mock_read):
        html = _chapter_html("h2", "Ch", 60)
        mock_read.return_value = _make_book([_make_item("OEBPS/ch01.xhtml", html)])

        chapters = parse_epub("fake.epub")
        self.assertEqual(chapters[0].epub_item_name, "OEBPS/ch01.xhtml")


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
    def test_epigraph_filename_title_fallback(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><p>{words}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("text/epigraph.xhtml", html)])
        self.assertEqual(parse_epub("f.epub")[0].title, "Epigraph")

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
    def test_skips_standard_ebooks_backmatter(self, mock_read):
        items = [
            _make_item("text/colophon.xhtml", _chapter_html("h2", "Colophon", 60)),
            _make_item("text/imprint.xhtml", _chapter_html("h2", "Imprint", 60)),
            _make_item("text/list-of-illustrations.xhtml", _chapter_html("h2", "List of Illustrations", 60)),
            _make_item("text/uncopyright.xhtml", _chapter_html("h2", "Uncopyright", 60)),
            _make_item("text/chapter-1.xhtml", _chapter_html("h2", "I", 60)),
        ]
        mock_read.return_value = _make_book(items)
        chapters = parse_epub("f.epub")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].title, "I")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_skips_exact_loi_basename(self, mock_read):
        items = [
            _make_item("text/loi.xhtml", _chapter_html("h2", "List of Illustrations", 60)),
            _make_item("text/chapter-1.xhtml", _chapter_html("h2", "I", 60)),
        ]
        mock_read.return_value = _make_book(items)
        self.assertEqual(len(parse_epub("f.epub")), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_loi_is_not_broad_substring_skip(self, mock_read):
        items = [
            _make_item("text/loitering.xhtml", _chapter_html("h2", "Loitering", 60)),
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
        # heading "Exact" = 1 word + 49 para words = 50 total
        html = _chapter_html("h2", "Exact", 49)
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
        # heading "T" = 1 word + 60 para words (anchor removed) = 61 total
        self.assertEqual(chapters[0].word_count, 61)

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
    def test_div_only_item_filtered(self, mock_read):
        # <div> is not a content tag — no elements extracted, chapter filtered out
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><div>{words}</div></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(len(parse_epub("f.epub")), 0)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_image_only_item(self, mock_read):
        html = '<html><body><img src="pic.jpg"/></body></html>'
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        self.assertEqual(len(parse_epub("f.epub")), 0)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_blockquote_extracted(self, mock_read):
        words = " ".join(f"w{i}" for i in range(50))
        html = f"<html><body><blockquote>{words}</blockquote></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        chapters = parse_epub("f.epub")
        self.assertEqual(len(chapters), 1)
        self.assertIn("w0", chapters[0].text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_nested_blockquote_paragraphs_not_duplicated(self, mock_read):
        stanza1 = " ".join(f"a{i}" for i in range(30))
        stanza2 = " ".join(f"b{i}" for i in range(30))
        html = (
            "<html><body><h1>T</h1><blockquote>"
            f"<p>{stanza1}</p><p>{stanza2}</p>"
            "</blockquote></body></html>"
        )
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])

        ch = parse_epub("f.epub")[0]

        self.assertEqual(ch.paragraphs, ["T", stanza1, stanza2])
        self.assertEqual(ch.text.count("a0"), 1)
        self.assertEqual(ch.text.count("b0"), 1)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_nested_list_paragraphs_not_duplicated(self, mock_read):
        para = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><ol><li><p>{para}</p></li></ol></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])

        ch = parse_epub("f.epub")[0]

        self.assertEqual(ch.paragraphs, [para])
        self.assertEqual(ch.text.count("w0"), 1)


class TestParseEpubParagraphs(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_paragraphs_field_is_list(self, mock_read):
        html = _chapter_html("h2", "Ch", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        self.assertIsInstance(ch.paragraphs, list)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_paragraphs_includes_heading(self, mock_read):
        # heading is now a spoken element, so it appears in paragraphs
        words_a = " ".join(f"a{i}" for i in range(30))
        words_b = " ".join(f"b{i}" for i in range(30))
        html = f"<html><body><h1>Title</h1><p>{words_a}</p><p>{words_b}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        # heading + 2 paragraphs = 3 spoken elements
        self.assertEqual(len(ch.paragraphs), 3)
        self.assertEqual(ch.paragraphs[0], "Title")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_paragraphs_join_equals_text(self, mock_read):
        html = _chapter_html("h2", "Ch", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        self.assertEqual("\n\n".join(ch.paragraphs), ch.text)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_no_double_newline_inside_paragraph(self, mock_read):
        words_a = " ".join(f"a{i}" for i in range(30))
        words_b = " ".join(f"b{i}" for i in range(30))
        html = f"<html><body><h1>T</h1><p>{words_a}</p><p>{words_b}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        for para in ch.paragraphs:
            self.assertNotIn("\n\n", para)


class TestParseEpubContentElements(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_elements_field_populated(self, mock_read):
        html = _chapter_html("h2", "My Chapter", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        self.assertIsInstance(ch.elements, list)
        self.assertGreater(len(ch.elements), 0)
        self.assertIsInstance(ch.elements[0], ContentElement)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_element_ids_sequential(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = f"<html><body><h2>Title</h2><p>{words}</p></body></html>"
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        self.assertEqual(ch.elements[0].id, "ch1-el0000")
        self.assertEqual(ch.elements[1].id, "ch1-el0001")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_element_ids_namespaced_per_chapter(self, mock_read):
        items = [
            _make_item("ch1.xhtml", _chapter_html("h2", "One", 60)),
            _make_item("ch2.xhtml", _chapter_html("h2", "Two", 60)),
        ]
        mock_read.return_value = _make_book(items)
        chapters = parse_epub("f.epub")
        self.assertTrue(chapters[0].elements[0].id.startswith("ch1-"))
        self.assertTrue(chapters[1].elements[0].id.startswith("ch2-"))

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_heading_element_spoken_true(self, mock_read):
        html = _chapter_html("h2", "My Chapter", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        heading_el = next(el for el in ch.elements if el.tag == "h2")
        self.assertTrue(heading_el.spoken)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_epub_type_footnote_spoken_false(self, mock_read):
        words = " ".join(f"w{i}" for i in range(60))
        html = (
            f'<html><body><p>{words}</p>'
            f'<aside epub:type="footnote"><p>foot note text here</p></aside>'
            f'</body></html>'
        )
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        footnote_els = [el for el in ch.elements if "foot" in el.text]
        self.assertTrue(all(not el.spoken for el in footnote_els))

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_element_html_contains_id(self, mock_read):
        html = _chapter_html("h2", "My Chapter", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        for el in ch.elements:
            self.assertIn(f'id="{el.id}"', el.html)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_element_text_matches_spoken_paragraphs(self, mock_read):
        html = _chapter_html("h2", "My Chapter", 60)
        mock_read.return_value = _make_book([_make_item("ch.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        spoken_texts = [el.text for el in ch.elements if el.spoken]
        self.assertEqual(spoken_texts, ch.paragraphs)


class TestNormalizeHeadingForTts(unittest.TestCase):

    def test_pure_roman_numeral(self):
        self.assertEqual(normalize_heading_for_tts("I"), "Chapter 1.")
        self.assertEqual(normalize_heading_for_tts("II"), "Chapter 2.")
        self.assertEqual(normalize_heading_for_tts("IV"), "Chapter 4.")
        self.assertEqual(normalize_heading_for_tts("XII"), "Chapter 12.")

    def test_roman_with_trailing_period(self):
        self.assertEqual(normalize_heading_for_tts("III."), "Chapter 3.")

    def test_pure_arabic_numeral(self):
        self.assertEqual(normalize_heading_for_tts("1"), "Chapter 1.")
        self.assertEqual(normalize_heading_for_tts("12"), "Chapter 12.")
        self.assertEqual(normalize_heading_for_tts("1."), "Chapter 1.")

    def test_chapter_prefix_with_roman(self):
        self.assertEqual(normalize_heading_for_tts("Chapter II"), "Chapter 2.")
        self.assertEqual(normalize_heading_for_tts("Chapter II."), "Chapter 2.")

    def test_chapter_prefix_with_arabic(self):
        self.assertEqual(normalize_heading_for_tts("Chapter 3"), "Chapter 3.")

    def test_roman_with_subtitle(self):
        self.assertEqual(
            normalize_heading_for_tts("III. The Storm"),
            "Chapter 3. The Storm.",
        )
        self.assertEqual(
            normalize_heading_for_tts("IV  The Storm"),
            "Chapter 4. The Storm.",
        )

    def test_arabic_with_subtitle(self):
        self.assertEqual(
            normalize_heading_for_tts("3. The Storm"),
            "Chapter 3. The Storm.",
        )

    def test_plain_title_unchanged(self):
        self.assertEqual(normalize_heading_for_tts("The Storm"), "The Storm")
        self.assertEqual(normalize_heading_for_tts("Prologue"), "Prologue")

    def test_empty(self):
        self.assertEqual(normalize_heading_for_tts(""), "")

    def test_invalid_roman_left_alone(self):
        # "MM" is valid roman (2000), but plain capitalized words should stay.
        self.assertEqual(normalize_heading_for_tts("Mary"), "Mary")


class TestParseEpubHeadingNormalization(unittest.TestCase):

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_roman_heading_text_normalized(self, mock_read_epub):
        html = "<html><body><h2>II</h2><p>" + " ".join(f"w{i}" for i in range(60)) + "</p></body></html>"
        mock_read_epub.return_value = _make_book([_make_item("ch1.xhtml", html)])
        ch = parse_epub("f.epub")[0]
        # First spoken element is the heading; its text is normalized for TTS.
        self.assertEqual(ch.elements[0].text, "Chapter 2.")
        # Display HTML is unchanged.
        self.assertIn("<h2", ch.elements[0].html)
        self.assertIn(">II<", ch.elements[0].html)
        # Chapter.title (used in manifest) is the original heading text.
        self.assertEqual(ch.title, "II")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_invisible_format_controls_removed_from_spoken_text(self, mock_read_epub):
        body = " ".join(f"word{i}" for i in range(60))
        dash = "-"
        html = f"<html><head><meta charset=\"utf-8\"></head><body><h2>VI</h2><p>Alice\ufeff\u2060{dash}{body}</p></body></html>"
        mock_read_epub.return_value = _make_book([_make_item("ch1.xhtml", html)])

        ch = parse_epub("f.epub")[0]

        self.assertNotIn("\ufeff", ch.text)
        self.assertNotIn("\u2060", ch.text)
        self.assertIn("Alice", ch.text)
        self.assertIn("word0", ch.text)
        self.assertIn(f"Alice{dash}word0", ch.text)

if __name__ == "__main__":
    unittest.main()
