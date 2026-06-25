"""Inject stable section element IDs into EPUB HTML."""

from __future__ import annotations

import io
import re

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from openshelf.pipeline.epub_parser import (
    Section,
    clean_extracted_text,
    iter_content_tags,
)


def annotate_epub(epub_path: str, sections: list[Section]) -> bytes:
    book = epub.read_epub(epub_path)
    sections_by_item: dict[str, list[Section]] = {}
    for section in sections:
        if section.epub_item_name:
            sections_by_item.setdefault(section.epub_item_name, []).append(section)

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        filename = item.get_name()
        item_sections = sections_by_item.get(filename)
        if not item_sections:
            continue

        expected = [
            element
            for section in sorted(item_sections, key=lambda value: value.sequence)
            for element in section.elements
        ]
        expected_index = 0
        soup = BeautifulSoup(item.get_content(), "html.parser")

        for tag in iter_content_tags(soup):
            for marker in tag.find_all(["sup", "sub"]):
                marker.decompose()
            for anchor in tag.find_all("a"):
                if anchor.string and re.fullmatch(r"\d+", anchor.string.strip()):
                    anchor.decompose()

            text = clean_extracted_text(tag.get_text(separator=" "))
            if not text:
                continue
            if expected_index >= len(expected):
                break

            element = expected[expected_index]
            if text != element.text:
                continue
            tag["id"] = element.id
            expected_index += 1

        item.set_content(str(soup).encode("utf-8"))

    _fix_toc_uids(book.toc)
    buffer = io.BytesIO()
    epub.write_epub(buffer, book)
    return buffer.getvalue()


def _fix_toc_uids(toc: list, counter: int = 0) -> int:
    for item in toc:
        if isinstance(item, tuple):
            section, children = item
            if hasattr(section, "uid") and section.uid is None:
                section.uid = f"navpoint-fix-{counter}"
                counter += 1
            counter = _fix_toc_uids(children, counter)
        elif hasattr(item, "uid") and item.uid is None:
            item.uid = f"navpoint-fix-{counter}"
            counter += 1
    return counter
