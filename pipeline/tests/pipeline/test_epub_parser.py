"""Tests for semantic section parsing and the version-2 book parse artifact."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.epub_parser import (  # noqa: E402
    BOOK_PARSE_VERSION,
    ContentElement,
    Section,
    SectionHeading,
    build_book_parse_artifact,
    parse_epub,
    read_book_parse_artifact,
    write_book_parse_artifact,
)


def _words(prefix: str, count: int = 60) -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def _item(item_id: str, name: str, html: str):
    item = MagicMock()
    item.get_id.return_value = item_id
    item.get_name.return_value = name
    item.get_content.return_value = html.encode("utf-8")
    return item


def _book(items, language: str = "en"):
    book = MagicMock()
    book.get_items_of_type.return_value = items
    book.get_item_with_id.side_effect = lambda item_id: next(
        (item for item in items if item.get_id() == item_id),
        None,
    )
    book.get_metadata.side_effect = lambda namespace, name: (
        [(language, {})] if name == "language" else []
    )
    book.spine = [(item.get_id(), "yes") for item in items]
    book.toc = []
    return book


def _section(sequence: int = 1) -> Section:
    heading_element = ContentElement(
        id=f"sec{sequence}-el0000",
        tag="h2",
        html="<h2>I</h2>",
        text="I",
        spoken=True,
    )
    body_element = ContentElement(
        id=f"sec{sequence}-el0001",
        tag="p",
        html="<p>Alice was beginning to get very tired.</p>",
        text="Alice was beginning to get very tired.",
        spoken=True,
    )
    return Section(
        sequence=sequence,
        section_type="chapter",
        ordinal=1,
        heading=SectionHeading(
            display_label="I",
            display_title="Down the Rabbit-Hole",
            spoken_text="Chapter One. Down the Rabbit-Hole.",
            element_ids=[heading_element.id],
        ),
        elements=[heading_element, body_element],
        body_elements=[body_element],
        paragraphs=[body_element.text],
        text=body_element.text,
        word_count=8,
        epub_item_name="text/chapter-1.xhtml",
    )


class TestSemanticSectionParsing(unittest.TestCase):
    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_roman_chapter_preserves_display_and_normalizes_spoken_heading(self, read_epub):
        body = _words("alice")
        read_epub.return_value = _book([
            _item(
                "ch1",
                "text/chapter-1.xhtml",
                (
                    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                    '<section epub:type="chapter"><h2>I</h2>'
                    '<h3>Down the Rabbit-Hole</h3>'
                    f"<p>{body}</p></section></body></html>"
                ),
            ),
        ])

        [section] = parse_epub("alice.epub")

        self.assertEqual(section.sequence, 1)
        self.assertEqual(section.section_type, "chapter")
        self.assertEqual(section.ordinal, 1)
        self.assertEqual(section.heading.display_label, "I")
        self.assertEqual(section.heading.display_title, "Down the Rabbit-Hole")
        self.assertEqual(
            section.heading.spoken_text,
            "Chapter One. Down the Rabbit-Hole.",
        )
        self.assertEqual(section.paragraphs, [body])
        self.assertNotIn("Down the Rabbit-Hole", section.text)
        self.assertEqual(section.heading.element_ids, ["sec1-el0000", "sec1-el0001"])
        self.assertEqual(section.body_elements[0].id, "sec1-el0002")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_special_section_is_not_renumbered_from_spine_position(self, read_epub):
        epilogue_body = _words("ending")
        chapter_body = _words("opening")
        read_epub.return_value = _book([
            _item(
                "ending",
                "text/epilogue.xhtml",
                (
                    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                    '<section epub:type="epilogue"><h2>Epilogue</h2>'
                    f"<p>{epilogue_body}</p></section></body></html>"
                ),
            ),
            _item(
                "chapter",
                "text/chapter-1.xhtml",
                (
                    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                    '<section epub:type="chapter"><h2>Chapter 1</h2>'
                    f"<p>{chapter_body}</p></section></body></html>"
                ),
            ),
        ])

        sections = parse_epub("book.epub")

        self.assertEqual(
            [(section.sequence, section.section_type, section.ordinal) for section in sections],
            [(1, "epilogue", None), (2, "chapter", 1)],
        )
        self.assertEqual(sections[0].heading.spoken_text, "Epilogue.")
        self.assertEqual(sections[1].heading.spoken_text, "Chapter One.")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_standalone_title_and_meaningful_leaf_div_are_preserved(self, read_epub):
        body = _words("tea")
        read_epub.return_value = _book([
            _item(
                "tea",
                "text/tea-party.xhtml",
                (
                    "<html><body><h2>A Mad Tea-Party</h2>"
                    f'<div class="letter">{body}</div></body></html>'
                ),
            ),
        ])

        [section] = parse_epub("alice.epub")

        self.assertIsNone(section.ordinal)
        self.assertEqual(section.section_type, "chapter")
        self.assertEqual(section.heading.display_title, "A Mad Tea-Party")
        self.assertEqual(section.heading.spoken_text, "A Mad Tea-Party")
        self.assertEqual(section.body_elements[0].tag, "div")
        self.assertEqual(section.text, body)

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_source_title_page_is_excluded(self, read_epub):
        read_epub.return_value = _book([
            _item(
                "title",
                "text/titlepage.xhtml",
                f"<html><body><h1>Alice</h1><p>{_words('title')}</p></body></html>",
            ),
            _item(
                "ch1",
                "text/chapter-1.xhtml",
                (
                    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                    '<section epub:type="chapter"><h2>I</h2>'
                    f"<p>{_words('body')}</p></section></body></html>"
                ),
            ),
        ])

        sections = parse_epub("alice.epub")

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].epub_item_name, "text/chapter-1.xhtml")

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_non_english_numbered_heading_preserves_source_for_speech(self, read_epub):
        read_epub.return_value = _book(
            [
                _item(
                    "ch1",
                    "text/chapter-1.xhtml",
                    (
                        '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                        '<section epub:type="chapter"><h2>I</h2>'
                        '<h3>Dans le terrier du lapin</h3>'
                        f"<p>{_words('corps')}</p></section></body></html>"
                    ),
                ),
            ],
            language="fr",
        )

        [section] = parse_epub("alice-fr.epub")

        self.assertEqual(
            section.heading.spoken_text,
            "I Dans le terrier du lapin.",
        )

    @patch("openshelf.pipeline.epub_parser.epub.read_epub")
    def test_epub_type_on_heading_classifies_named_section(self, read_epub):
        read_epub.return_value = _book([
            _item(
                "ending",
                "text/ending.xhtml",
                (
                    '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                    '<h2 epub:type="epilogue">After the Story</h2>'
                    f"<p>{_words('ending')}</p></body></html>"
                ),
            ),
        ])

        [section] = parse_epub("book.epub")

        self.assertEqual(section.section_type, "epilogue")
        self.assertIsNone(section.ordinal)
        self.assertEqual(section.heading.display_title, "After the Story")


class TestAliceRegression(unittest.TestCase):
    def test_real_standard_ebooks_alice_keeps_epigraph_and_twelve_ordinals(self):
        root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        alice_path = os.path.join(
            root,
            "download",
            "books",
            "standard-ebooks",
            "lewis-carroll",
            "alices-adventures-in-wonderland.epub",
        )
        if not os.path.exists(alice_path):
            self.skipTest("local Standard Ebooks Alice fixture is not available")

        sections = parse_epub(alice_path)

        self.assertEqual(sections[0].section_type, "epigraph")
        chapters = [section for section in sections if section.section_type == "chapter"]
        self.assertEqual([section.ordinal for section in chapters], list(range(1, 13)))
        self.assertTrue(chapters[0].text.startswith("Alice was beginning"))
        self.assertNotIn(chapters[0].heading.display_title, chapters[0].paragraphs[0])


class TestBookParseArtifact(unittest.TestCase):
    def test_v2_artifact_keeps_heading_separate_from_elements(self):
        artifact = build_book_parse_artifact(
            [_section()],
            "sha256:abc",
            {"title": "Alice", "author": "Lewis Carroll", "language": "en"},
        )

        self.assertEqual(artifact["version"], BOOK_PARSE_VERSION)
        self.assertNotIn("chapters", artifact)
        section = artifact["sections"][0]
        self.assertEqual(section["sequence"], 1)
        self.assertEqual(section["section_type"], "chapter")
        self.assertEqual(section["heading"]["display_label"], "I")
        self.assertEqual(section["heading"]["spoken_text"], "Chapter One. Down the Rabbit-Hole.")

    def test_write_round_trips_and_rejects_v1(self):
        artifact = build_book_parse_artifact([_section()], "sha256:abc", {})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "book.parse.json")
            write_book_parse_artifact(path, artifact)
            self.assertEqual(read_book_parse_artifact(path), artifact)

            with open(path, "w", encoding="utf-8") as file:
                file.write('{"version": 1, "chapters": []}')
            with self.assertRaisesRegex(ValueError, "expected 2"):
                read_book_parse_artifact(path)


if __name__ == "__main__":
    unittest.main()
