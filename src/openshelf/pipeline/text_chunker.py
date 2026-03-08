"""Step 2: Split chapter text into paragraph-aware chunks for TTS."""

import re

from openshelf.config import CHUNK_MAX_WORDS

_ABBREVIATIONS = re.compile(
    r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Gen|Gov|Sgt|Cpl|Pvt|Rev|Hon|Pres"
    r"|Vol|Dept|Univ|Inc|Corp|Ltd|Co|vs|etc|approx"
    r"|Mt|Ft|Capt|Lt|Maj|Col"
    r"|[A-Z])\."
)
_PLACEHOLDER = "\x00"


def _protect_abbreviations(text: str) -> str:
    return _ABBREVIATIONS.sub(lambda m: m.group(1) + _PLACEHOLDER, text)


def _restore_abbreviations(text: str) -> str:
    return text.replace(_PLACEHOLDER, ".")


def _split_sentences(text: str) -> list[str]:
    protected = _protect_abbreviations(text)
    raw = re.split(r"(?<=[.!?])\s+", protected)
    return [_restore_abbreviations(s) for s in raw if s.strip()]


def _split_at_commas(sentence: str, max_words: int) -> list[str]:
    parts = sentence.split(", ")
    chunks: list[str] = []
    current: list[str] = []
    current_wc = 0

    for part in parts:
        wc = len(part.split())
        if current and current_wc + wc > max_words:
            chunks.append(", ".join(current))
            current = [part]
            current_wc = wc
        else:
            current.append(part)
            current_wc += wc

    if current:
        joined = ", ".join(current)
        if len(joined.split()) > max_words:
            chunks.extend(_split_at_words(joined, max_words))
        else:
            chunks.append(joined)

    return chunks


def _split_at_words(fragment: str, max_words: int) -> list[str]:
    words = fragment.split()
    chunks: list[str] = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i : i + max_words]))
    return chunks


def _chunk_paragraph(paragraph: str, max_words: int) -> list[str]:
    """Split an oversized paragraph at sentence boundaries."""
    sentences = _split_sentences(paragraph)
    if not sentences:
        return [paragraph] if paragraph.strip() else []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_wc = 0

    for sentence in sentences:
        s_wc = len(sentence.split())

        if current_wc + s_wc <= max_words:
            current_parts.append(sentence)
            current_wc += s_wc
        else:
            if current_parts:
                chunks.append(" ".join(current_parts))
                current_parts = []
                current_wc = 0

            if s_wc <= max_words:
                current_parts.append(sentence)
                current_wc = s_wc
            else:
                if ", " in sentence:
                    chunks.extend(_split_at_commas(sentence, max_words))
                else:
                    chunks.extend(_split_at_words(sentence, max_words))

    if current_parts:
        chunks.append(" ".join(current_parts))

    return chunks


def chunk_text(text: str, max_words: int = CHUNK_MAX_WORDS) -> list[str]:
    if not text or not text.strip():
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    # Pre-split oversized paragraphs into sub-chunks
    para_units: list[str] = []
    for para in paragraphs:
        wc = len(para.split())
        if wc <= max_words:
            para_units.append(para)
        else:
            para_units.extend(_chunk_paragraph(para, max_words))

    # Greedily pack paragraph units into chunks
    chunks: list[str] = []
    current_parts: list[str] = []
    current_wc = 0

    for unit in para_units:
        u_wc = len(unit.split())
        if current_wc + u_wc <= max_words:
            current_parts.append(unit)
            current_wc += u_wc
        else:
            if current_parts:
                chunks.append("\n\n".join(current_parts))
            current_parts = [unit]
            current_wc = u_wc

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks
