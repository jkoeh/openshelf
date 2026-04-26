"""Step 2: Split chapter text into paragraph-aware chunks for TTS."""

from __future__ import annotations

import re
from dataclasses import dataclass

from openshelf.config import CHUNK_MAX_WORDS

_ABBREVIATIONS = re.compile(
    r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Gen|Gov|Sgt|Cpl|Pvt|Rev|Hon|Pres"
    r"|Vol|Dept|Univ|Inc|Corp|Ltd|Co|vs|etc|approx"
    r"|Mt|Ft|Capt|Lt|Maj|Col"
    r"|[A-Z])\."
)
_PLACEHOLDER = "\x00"


@dataclass
class Chunk:
    text: str
    para_start: int   # index of first spoken element in Chapter.paragraphs
    para_end: int     # index of last spoken element covered (inclusive)
    el_start: str = ""  # element ID of first spoken element in chunk
    el_end: str = ""    # element ID of last spoken element in chunk


def _protect_abbreviations(text: str) -> str:
    return _ABBREVIATIONS.sub(lambda m: m.group(1) + _PLACEHOLDER, text)


def _restore_abbreviations(text: str) -> str:
    return text.replace(_PLACEHOLDER, ".")


def _split_sentences(text: str) -> list[str]:
    protected = _protect_abbreviations(text)
    raw = re.split(r"(?<=[.!?])\s+", protected)
    return [_restore_abbreviations(s) for s in raw if s.strip()]


def extract_trailing_sentences(text: str, n: int = 2) -> str:
    """Return the last *n* sentences of *text*, for use as TTS context overlap."""
    sentences = _split_sentences(text)
    if len(sentences) <= n:
        return text
    return " ".join(sentences[-n:])


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


def chunk_text(
    paragraphs: list[str],
    max_words: int = CHUNK_MAX_WORDS,
    element_ids: list[str] | None = None,
) -> list[Chunk]:
    if element_ids is not None and len(element_ids) != len(paragraphs):
        raise ValueError(
            f"element_ids length {len(element_ids)} must match paragraphs length {len(paragraphs)}"
        )

    if not paragraphs:
        return []

    # Pre-split oversized paragraphs into sub-units, tracking original para index
    # Each unit is (text, original_para_idx)
    para_units: list[tuple[str, int]] = []
    for para_idx, para in enumerate(paragraphs):
        para = para.strip()
        if not para:
            continue
        wc = len(para.split())
        if wc <= max_words:
            para_units.append((para, para_idx))
        else:
            for sub in _chunk_paragraph(para, max_words):
                para_units.append((sub, para_idx))

    if not para_units:
        return []

    # Greedily pack paragraph units into Chunks, tracking para_start/para_end
    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_wc = 0
    current_para_start: int = para_units[0][1]
    current_para_end: int = para_units[0][1]

    for unit_text, para_idx in para_units:
        u_wc = len(unit_text.split())
        if current_wc + u_wc <= max_words:
            current_parts.append(unit_text)
            current_wc += u_wc
            current_para_end = para_idx
        else:
            if current_parts:
                chunks.append(Chunk(
                    text="\n\n".join(current_parts),
                    para_start=current_para_start,
                    para_end=current_para_end,
                    el_start=element_ids[current_para_start] if element_ids else "",
                    el_end=element_ids[current_para_end] if element_ids else "",
                ))
            current_parts = [unit_text]
            current_wc = u_wc
            current_para_start = para_idx
            current_para_end = para_idx

    if current_parts:
        chunks.append(Chunk(
            text="\n\n".join(current_parts),
            para_start=current_para_start,
            para_end=current_para_end,
            el_start=element_ids[current_para_start] if element_ids else "",
            el_end=element_ids[current_para_end] if element_ids else "",
        ))

    return chunks
