"""Inject stable id attributes into EPUB HTML for client-side text/audio sync."""

from __future__ import annotations

import io
import re

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

from openshelf.pipeline.epub_parser import (
    Chapter,
    _CONTENT_TAGS,
)


def annotate_epub(epub_path: str, chapters: list[Chapter]) -> bytes:
    """Inject stable id attributes into EPUB HTML elements.

    Walks the EPUB items in the same order as parse_epub, matches each
    chapter to its source item via epub_item_name, then injects the element
    IDs from chapter.elements into the corresponding HTML tags (in document
    order, skipping empty elements — same logic as _extract_content_elements).

    Returns the modified EPUB as bytes (a valid EPUB file).
    """
    book = epub.read_epub(epub_path)

    # Build a lookup: item filename → Chapter
    chapter_by_item: dict[str, Chapter] = {
        ch.epub_item_name: ch for ch in chapters if ch.epub_item_name
    }

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        filename = item.get_name()
        chapter = chapter_by_item.get(filename)
        if chapter is None:
            continue  # item was skipped or filtered during parse_epub

        soup = BeautifulSoup(item.get_content(), "html.parser")

        # Walk content tags in document order, skip empty — mirrors _extract_content_elements
        el_iter = iter(chapter.elements)
        for tag in soup.find_all(_CONTENT_TAGS):
            # Apply same cleanup as _extract_content_elements so empty-check matches
            for marker in tag.find_all(["sup", "sub"]):
                marker.decompose()
            for anchor in tag.find_all("a"):
                if anchor.string and re.match(r"^\d+$", anchor.string.strip()):
                    anchor.decompose()

            text = re.sub(r"\s+", " ", tag.get_text()).strip()
            if not text:
                continue  # empty element was skipped in parse_epub too

            element = next(el_iter, None)
            if element is None:
                break  # exhausted elements for this chapter

            tag["id"] = element.id

        item.set_content(str(soup).encode("utf-8"))

    # Fix ebooklib bug: TOC entries with uid=None crash the NCX writer
    _fix_toc_uids(book.toc)

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


def _fix_toc_uids(toc: list, counter: int = 0) -> int:
    """Assign UIDs to TOC entries that have None as uid."""
    for item in toc:
        if isinstance(item, tuple):
            # Section: (Section, [children])
            section, children = item
            if hasattr(section, "uid") and section.uid is None:
                section.uid = f"navpoint-fix-{counter}"
                counter += 1
            counter = _fix_toc_uids(children, counter)
        elif hasattr(item, "uid") and item.uid is None:
            item.uid = f"navpoint-fix-{counter}"
            counter += 1
    return counter
