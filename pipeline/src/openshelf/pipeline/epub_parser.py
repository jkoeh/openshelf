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


_SKIP_PATTERNS = (
    "nav",
    "toc",
    "cover",
    "colophon",
    "imprint",
    "illustration",
    "copyright",
    "uncopyright",
)
_SKIP_BASENAMES = {"loi"}
_MIN_WORD_COUNT = 50
_CONTENT_TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li", "figcaption")
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIP_EPUB_TYPES = frozenset({"footnote", "endnote", "toc", "pagebreak"})
_FILENAME_TITLES = {
    "epigraph": "Epigraph",
}

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_INVISIBLE_TEXT_RE = re.compile("[\ufeff\u200b\u200c\u200d\u2060]")


def _roman_to_arabic(roman: str) -> int | None:
    """Convert a Roman numeral string to an int, or None if invalid."""
    if not roman:
        return None
    total = 0
    prev = 0
    for ch in roman.upper()[::-1]:
        v = _ROMAN_VALUES.get(ch)
        if v is None:
            return None
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total if total > 0 else None


def normalize_heading_for_tts(text: str) -> str:
    """Rewrite numeric/roman headings so Kokoro reads them as words, not letters.

    "II" -> "Chapter 2.", "3" -> "Chapter 3.", "III. The Storm" -> "Chapter 3. The Storm.",
    "Chapter II" -> "Chapter 2.". Plain titles are returned unchanged.
    """
    stripped = text.strip()
    if not stripped:
        return text

    # "Chapter <roman/arabic>" (with optional trailing punctuation)
    m = re.match(r"^Chapter\s+([IVXLCDM]+|\d+)\.?$", stripped, re.IGNORECASE)
    if m:
        token = m.group(1)
        n = int(token) if token.isdigit() else _roman_to_arabic(token)
        if n:
            return f"Chapter {n}."

    # Strip a single trailing period for the bare-numeral check
    bare = stripped.rstrip(".")

    # Pure Roman numeral
    if re.fullmatch(r"[IVXLCDM]+", bare, re.IGNORECASE):
        n = _roman_to_arabic(bare)
        if n:
            return f"Chapter {n}."

    # Pure Arabic numeral
    if re.fullmatch(r"\d+", bare):
        return f"Chapter {bare}."

    # "<roman/arabic><sep><rest>"  — e.g. "III. The Storm" or "3 The Storm"
    m = re.match(r"^([IVXLCDM]+|\d+)[.\s]+(.+)$", stripped)
    if m:
        token, rest = m.group(1), m.group(2).strip().rstrip(".")
        n = int(token) if token.isdigit() else _roman_to_arabic(token)
        if n and rest:
            return f"Chapter {n}. {rest}."

    return text


def clean_extracted_text(text: str) -> str:
    """Remove non-spoken format controls and normalize element whitespace."""
    without_format_controls = _INVISIBLE_TEXT_RE.sub("", text)
    return re.sub(r"\s+", " ", without_format_controls).strip()


def _should_skip(filename: str) -> bool:
    name_lower = filename.lower()
    stem = name_lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem in _SKIP_BASENAMES or any(p in name_lower for p in _SKIP_PATTERNS)


def _document_items(book) -> list:
    """Return document items in EPUB spine order when possible."""
    items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    spine = getattr(book, "spine", None)
    if not isinstance(spine, (list, tuple)):
        return items

    by_name = {item.get_name(): item for item in items}
    by_id = {}
    for item in items:
        try:
            by_id[item.get_id()] = item
        except Exception:
            continue

    ordered = []
    seen: set[str] = set()
    for entry in spine:
        item_id = entry[0] if isinstance(entry, (list, tuple)) else entry
        item = by_id.get(item_id)
        if item is None:
            try:
                item = book.get_item_with_id(item_id)
            except Exception:
                item = None
        if item is None:
            continue
        name = item.get_name()
        if name in by_name and name not in seen:
            ordered.append(item)
            seen.add(name)

    ordered.extend(item for item in items if item.get_name() not in seen)
    return ordered


def _filename_stem(filename: str) -> str:
    return filename.lower().rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _extract_title(soup: BeautifulSoup, fallback_number: int, filename: str = "") -> str:
    for tag in ("h1", "h2", "h3"):
        heading = soup.find(tag)
        if heading:
            return re.sub(r"\s+", " ", heading.get_text(separator=" ")).strip()
    filename_title = _FILENAME_TITLES.get(_filename_stem(filename))
    if filename_title:
        return filename_title
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


def _has_nested_content_tag(tag) -> bool:
    return tag.find(_CONTENT_TAGS) is not None


def iter_content_tags(soup: BeautifulSoup):
    """Yield content tags without duplicating nested content containers."""
    for tag in soup.find_all(_CONTENT_TAGS):
        if _has_nested_content_tag(tag):
            continue
        yield tag


def _extract_content_elements(soup: BeautifulSoup, chapter_num: int) -> list[ContentElement]:
    elements: list[ContentElement] = []
    idx = 0

    for tag in iter_content_tags(soup):
        # Remove footnote markers and numeric anchors from this element's content
        for marker in tag.find_all(["sup", "sub"]):
            marker.decompose()
        for anchor in tag.find_all("a"):
            if anchor.string and re.match(r"^\d+$", anchor.string.strip()):
                anchor.decompose()

        text = clean_extracted_text(tag.get_text())
        if not text:
            continue

        if tag.name in _HEADING_TAGS:
            text = normalize_heading_for_tts(text)

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
    for tag in iter_content_tags(soup):
        text = clean_extracted_text(tag.get_text())
        if text and _is_spoken(tag):
            words += len(text.split())
    return words


def extract_cover_image(epub_path: str) -> tuple[bytes, str] | None:
    """Extract the cover image from an EPUB. Returns (image_bytes, media_type) or None.

    Tries in order:
    1. ebooklib ITEM_COVER type (Gutenberg uses this)
    2. OPF metadata cover reference (Standard Ebooks uses this)
    3. Any image/cover item with 'cover' in the name
    4. Largest image as fallback
    """
    book = epub.read_epub(epub_path)

    # 1. Check for ITEM_COVER (ebooklib type 10) — Gutenberg EPUBs use this
    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        content = item.get_content()
        media_type = item.media_type or _guess_media_type(item.get_name())
        if content:
            return content, media_type

    # 2. Check metadata for cover reference (OPF <meta name="cover" content="...">)
    cover_meta = book.get_metadata("OPF", "cover")
    if cover_meta:
        cover_id = cover_meta[0][1].get("content", "") if len(cover_meta[0]) > 1 else cover_meta[0][0]
        item = book.get_item_with_id(cover_id)
        if item and hasattr(item, "get_content"):
            media_type = item.media_type or _guess_media_type(item.get_name())
            return item.get_content(), media_type

    # 3. Look for items with 'cover' in the name (across all image types)
    for item_type in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER):
        for item in book.get_items_of_type(item_type):
            name = item.get_name().lower()
            if "cover" in name:
                media_type = item.media_type or _guess_media_type(item.get_name())
                return item.get_content(), media_type

    # 4. Fall back to the largest image (likely the cover)
    images = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
    if images:
        largest = max(images, key=lambda i: len(i.get_content()))
        if len(largest.get_content()) > 5000:  # skip tiny icons
            media_type = largest.media_type or _guess_media_type(largest.get_name())
            return largest.get_content(), media_type

    return None


def _guess_media_type(filename: str) -> str:
    """Guess media type from filename when ebooklib doesn't provide one."""
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def parse_epub(epub_path: str) -> list[Chapter]:
    book = epub.read_epub(epub_path)
    items = _document_items(book)

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
        title = _extract_title(soup, chapter_num, filename)

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
