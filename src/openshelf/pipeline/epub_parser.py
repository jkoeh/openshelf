"""Step 1: Parse EPUB files into chapters of clean plain text."""

import re
from dataclasses import dataclass

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup


@dataclass
class Chapter:
    number: int
    title: str
    text: str
    word_count: int


_SKIP_PATTERNS = ("nav", "toc", "cover", "titlepage")
_MIN_WORD_COUNT = 50


def _should_skip(filename: str) -> bool:
    name_lower = filename.lower()
    return any(p in name_lower for p in _SKIP_PATTERNS)


def _extract_title(soup: BeautifulSoup, fallback_number: int) -> str:
    for tag in ("h1", "h2", "h3"):
        heading = soup.find(tag)
        if heading:
            return heading.get_text(strip=True)
    return f"Chapter {fallback_number}"


def _extract_text(soup: BeautifulSoup) -> str:
    # Remove footnote markers
    for tag in soup.find_all(["sup", "sub"]):
        tag.decompose()

    # Remove numeric-only anchor tags (internal cross-references like [1])
    for tag in soup.find_all("a"):
        if tag.string and re.match(r"^\d+$", tag.string.strip()):
            tag.decompose()

    paragraphs = soup.find_all("p")
    cleaned = []
    for p in paragraphs:
        text = p.get_text()
        # Normalize whitespace within paragraph
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cleaned.append(text)

    return "\n\n".join(cleaned)


def parse_epub(epub_path: str) -> list[Chapter]:
    book = epub.read_epub(epub_path)
    items = book.get_items_of_type(ebooklib.ITEM_DOCUMENT)

    chapters: list[Chapter] = []
    chapter_num = 0

    for item in items:
        filename = item.get_name()
        if _should_skip(filename):
            continue

        content = item.get_content()
        soup = BeautifulSoup(content, "html.parser")

        text = _extract_text(soup)
        wc = len(text.split())

        if wc < _MIN_WORD_COUNT:
            continue

        chapter_num += 1
        title = _extract_title(soup, chapter_num)

        chapters.append(Chapter(
            number=chapter_num,
            title=title,
            text=text,
            word_count=wc,
        ))

    return chapters
