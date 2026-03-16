"""Tests for epub_annotator — injects stable id attributes into EPUB HTML."""

import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, call

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.epub_annotator import annotate_epub
from openshelf.pipeline.epub_parser import Chapter, ContentElement


# --- Test helpers ---

def _make_content_element(id: str, tag: str = "p", text: str = "hello", spoken: bool = True) -> ContentElement:
    return ContentElement(id=id, tag=tag, html=f"<{tag}>{text}</{tag}>", text=text, spoken=spoken)


def _make_chapter(number: int, item_name: str, elements: list[ContentElement]) -> Chapter:
    paragraphs = [el.text for el in elements if el.spoken]
    return Chapter(
        number=number,
        title="Test",
        elements=elements,
        paragraphs=paragraphs,
        text="\n\n".join(paragraphs),
        word_count=len(" ".join(paragraphs).split()),
        epub_item_name=item_name,
    )


def _make_epub_item(name: str, html: str):
    item = MagicMock()
    item.get_name.return_value = name
    item.get_content.return_value = html.encode("utf-8")
    item.set_content = MagicMock()
    return item


def _make_epub_book(items: list):
    book = MagicMock()
    book.get_items_of_type.return_value = items
    return book


class TestAnnotateEpubReturnType(unittest.TestCase):

    @patch("openshelf.pipeline.epub_annotator.epub.write_epub")
    @patch("openshelf.pipeline.epub_annotator.epub.read_epub")
    def test_returns_bytes(self, mock_read, mock_write):
        html = "<html><body><p>Hello world.</p></body></html>"
        item = _make_epub_item("ch01.xhtml", html)
        mock_read.return_value = _make_epub_book([item])

        def write_side_effect(buf, book):
            buf.write(b"fake epub bytes")
        mock_write.side_effect = write_side_effect

        chapter = _make_chapter(1, "ch01.xhtml", [
            _make_content_element("ch1-el0000", "p", "Hello world."),
        ])
        result = annotate_epub("fake.epub", [chapter])
        self.assertIsInstance(result, bytes)


class TestAnnotateEpubIdInjection(unittest.TestCase):

    @patch("openshelf.pipeline.epub_annotator.epub.write_epub")
    @patch("openshelf.pipeline.epub_annotator.epub.read_epub")
    def test_injects_id_into_p_tag(self, mock_read, mock_write):
        html = "<html><body><p>Hello world.</p></body></html>"
        item = _make_epub_item("ch01.xhtml", html)
        mock_read.return_value = _make_epub_book([item])
        mock_write.side_effect = lambda buf, book: buf.write(b"")

        chapter = _make_chapter(1, "ch01.xhtml", [
            _make_content_element("ch1-el0000", "p", "Hello world."),
        ])
        annotate_epub("fake.epub", [chapter])

        item.set_content.assert_called_once()
        injected_html = item.set_content.call_args[0][0].decode("utf-8")
        self.assertIn('id="ch1-el0000"', injected_html)

    @patch("openshelf.pipeline.epub_annotator.epub.write_epub")
    @patch("openshelf.pipeline.epub_annotator.epub.read_epub")
    def test_injects_id_into_heading(self, mock_read, mock_write):
        words = " ".join(f"w{i}" for i in range(30))
        html = f"<html><body><h2>Chapter One</h2><p>{words}</p></body></html>"
        item = _make_epub_item("ch01.xhtml", html)
        mock_read.return_value = _make_epub_book([item])
        mock_write.side_effect = lambda buf, book: buf.write(b"")

        chapter = _make_chapter(1, "ch01.xhtml", [
            _make_content_element("ch1-el0000", "h2", "Chapter One"),
            _make_content_element("ch1-el0001", "p", words),
        ])
        annotate_epub("fake.epub", [chapter])

        injected_html = item.set_content.call_args[0][0].decode("utf-8")
        self.assertIn('id="ch1-el0000"', injected_html)
        self.assertIn('id="ch1-el0001"', injected_html)

    @patch("openshelf.pipeline.epub_annotator.epub.write_epub")
    @patch("openshelf.pipeline.epub_annotator.epub.read_epub")
    def test_multiple_elements_get_sequential_ids(self, mock_read, mock_write):
        html = "<html><body><p>Para one.</p><p>Para two.</p><p>Para three.</p></body></html>"
        item = _make_epub_item("ch01.xhtml", html)
        mock_read.return_value = _make_epub_book([item])
        mock_write.side_effect = lambda buf, book: buf.write(b"")

        chapter = _make_chapter(1, "ch01.xhtml", [
            _make_content_element("ch1-el0000", "p", "Para one."),
            _make_content_element("ch1-el0001", "p", "Para two."),
            _make_content_element("ch1-el0002", "p", "Para three."),
        ])
        annotate_epub("fake.epub", [chapter])

        injected = item.set_content.call_args[0][0].decode("utf-8")
        self.assertIn('id="ch1-el0000"', injected)
        self.assertIn('id="ch1-el0001"', injected)
        self.assertIn('id="ch1-el0002"', injected)


class TestAnnotateEpubSkippedItems(unittest.TestCase):

    @patch("openshelf.pipeline.epub_annotator.epub.write_epub")
    @patch("openshelf.pipeline.epub_annotator.epub.read_epub")
    def test_item_not_in_chapters_not_modified(self, mock_read, mock_write):
        # nav.xhtml was skipped during parse_epub, so no chapter for it
        nav_item = _make_epub_item("nav.xhtml", "<html><body><nav>...</nav></body></html>")
        ch_item = _make_epub_item("ch01.xhtml", "<html><body><p>Hello world.</p></body></html>")
        mock_read.return_value = _make_epub_book([nav_item, ch_item])
        mock_write.side_effect = lambda buf, book: buf.write(b"")

        chapter = _make_chapter(1, "ch01.xhtml", [
            _make_content_element("ch1-el0000", "p", "Hello world."),
        ])
        annotate_epub("fake.epub", [chapter])

        # nav.xhtml should not have set_content called
        nav_item.set_content.assert_not_called()
        ch_item.set_content.assert_called_once()

    @patch("openshelf.pipeline.epub_annotator.epub.write_epub")
    @patch("openshelf.pipeline.epub_annotator.epub.read_epub")
    def test_multiple_chapters_each_annotated(self, mock_read, mock_write):
        words_a = " ".join(f"a{i}" for i in range(10))
        words_b = " ".join(f"b{i}" for i in range(10))
        item_a = _make_epub_item("ch01.xhtml", f"<html><body><p>{words_a}</p></body></html>")
        item_b = _make_epub_item("ch02.xhtml", f"<html><body><p>{words_b}</p></body></html>")
        mock_read.return_value = _make_epub_book([item_a, item_b])
        mock_write.side_effect = lambda buf, book: buf.write(b"")

        chapters = [
            _make_chapter(1, "ch01.xhtml", [_make_content_element("ch1-el0000", "p", words_a)]),
            _make_chapter(2, "ch02.xhtml", [_make_content_element("ch2-el0000", "p", words_b)]),
        ]
        annotate_epub("fake.epub", chapters)

        self.assertEqual(item_a.set_content.call_count, 1)
        self.assertEqual(item_b.set_content.call_count, 1)

        html_a = item_a.set_content.call_args[0][0].decode("utf-8")
        html_b = item_b.set_content.call_args[0][0].decode("utf-8")
        self.assertIn('id="ch1-el0000"', html_a)
        self.assertIn('id="ch2-el0000"', html_b)


if __name__ == "__main__":
    unittest.main()
