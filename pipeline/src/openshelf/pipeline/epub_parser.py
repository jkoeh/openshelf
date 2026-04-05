"""Step 1: Parse EPUB files into chapters of clean plain text."""

import re
from dataclasses import dataclass

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup


@dataclass
class ContentElement:
    id: str        # "ch3-el0012"
    tag: str       # "p", "h2", "blockquote", "li", "figcaption"
    html: str      # outer HTML with id attribute injected
    text: str      # plain text (sup/sub/numeric-a removed)
    spoken: bool   # True = goes to TTS


@dataclass
class Chapter:
    number: int
    title: str
    elements: list[ContentElement]  # all content elements with stable IDs
    paragraphs: list[str]   # [el.text for el in elements if el.spoken]
    text: str               # "\n\n".join(paragraphs)
    word_count: int
    epub_item_name: str = ""  # EPUB document item filename (for annotate_epub)


_SKIP_PATTERNS = ("nav", "toc", "cover")
_MIN_WORD_COUNT = 50
_CONTENT_TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "figcaption")
_SKIP_EPUB_TYPES = frozenset({"footnote", "endnote", "toc", "pagebreak"})


def _should_skip(filename: str) -> bool:
    name_lower = filename.lower()
    return any(p in name_lower for p in _SKIP_PATTERNS)


def _extract_title(soup: BeautifulSoup, fallback_number: int) -> str:
    for tag in ("h1", "h2", "h3"):
        heading = soup.find(tag)
        if heading:
            return re.sub(r"\s+", " ", heading.get_text(separator=" ")).strip()
    return f"Chapter {fallback_number}"


def _is_spoken(element) -> bool:
    """Return False if the element or any ancestor has an epub:type marking it as non-content."""
    for node in [element] + list(element.parents):
        epub_type = node.get("epub:type", "") if hasattr(node, "get") else ""
        if epub_type:
            types = epub_type.split()
            if any(t in _SKIP_EPUB_TYPES for t in types):
                return False
    return True


def _extract_content_elements(soup: BeautifulSoup, chapter_num: int) -> list[ContentElement]:
    elements: list[ContentElement] = []
    idx = 0

    for tag in soup.find_all(_CONTENT_TAGS):
        # Remove footnote markers and numeric anchors from this element's content
        for marker in tag.find_all(["sup", "sub"]):
            marker.decompose()
        for anchor in tag.find_all("a"):
            if anchor.string and re.match(r"^\d+$", anchor.string.strip()):
                anchor.decompose()

        text = re.sub(r"\s+", " ", tag.get_text()).strip()
        if not text:
            continue

        element_id = f"ch{chapter_num}-el{idx:04d}"
        tag["id"] = element_id
        html = str(tag)
        spoken = _is_spoken(tag)

        elements.append(ContentElement(
            id=element_id,
            tag=tag.name,
            html=html,
            text=text,
            spoken=spoken,
        ))
        idx += 1

    return elements


def _item_word_count(soup: BeautifulSoup) -> int:
    """Quick spoken word count from soup without modifying it (for pre-filter)."""
    words = 0
    for tag in soup.find_all(_CONTENT_TAGS):
        text = re.sub(r"\s+", " ", tag.get_text()).strip()
        if text and _is_spoken(tag):
            words += len(text.split())
    return words


def extract_cover_image(epub_path: str) -> tuple[bytes, str] | None:
    """Extract the cover image from an EPUB. Returns (image_bytes, media_type) or None.

    Tries in order: metadata cover reference, item named 'cover', largest image.
    """
    book = epub.read_epub(epub_path)

    # 1. Check metadata for cover reference (OPF <meta name="cover" content="...">)
    cover_meta = book.get_metadata("OPF", "cover")
    if cover_meta:
        cover_id = cover_meta[0][1].get("content", "") if len(cover_meta[0]) > 1 else cover_meta[0][0]
        item = book.get_item_with_id(cover_id)
        if item and hasattr(item, "get_content"):
            return item.get_content(), item.media_type

    # 2. Look for items with 'cover' in the name
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        name = item.get_name().lower()
        if "cover" in name:
            return item.get_content(), item.media_type

    # 3. Fall back to the largest image (likely the cover)
    images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
    if images:
        largest = max(images, key=lambda i: len(i.get_content()))
        if len(largest.get_content()) > 5000:  # skip tiny icons
            return largest.get_content(), largest.media_type

    return None


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

        # Pre-check word count before assigning chapter number (avoids ID gaps)
        if _item_word_count(soup) < _MIN_WORD_COUNT:
            continue

        chapter_num += 1
        # _item_word_count does not modify soup, so reuse it here
        elements = _extract_content_elements(soup, chapter_num)
        paragraphs = [el.text for el in elements if el.spoken]
        text = "\n\n".join(paragraphs)
        wc = len(text.split())
        title = _extract_title(soup, chapter_num)

        chapters.append(Chapter(
            number=chapter_num,
            title=title,
            elements=elements,
            paragraphs=paragraphs,
            text=text,
            word_count=wc,
            epub_item_name=filename,
        ))

    return chapters
