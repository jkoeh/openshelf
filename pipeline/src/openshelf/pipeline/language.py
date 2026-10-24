"""Language normalization and original-text units shared by synthesis and sync."""

import re

HAN = r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U0003134f]"
_HAN = re.compile(HAN)
_UNITS = re.compile(HAN + rf"|(?:(?!{HAN})[^\W_])+(?:['’](?:(?!{HAN})[^\W_])+)*")


def normalize_language(language: str) -> str:
    value = language.lower().replace("_", "-").split("-")[0]
    return {"a": "en", "b": "en", "eng": "en", "z": "zh", "zho": "zh", "chi": "zh"}.get(value, value)


def has_han(text: str) -> bool:
    return bool(_HAN.search(text))


def unit_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _UNITS.finditer(text)]


def unit_count(text: str) -> int:
    return len(unit_spans(text)) if has_han(text) else len(text.split())


def split_sentences(text: str) -> list[str]:
    if has_han(text):
        return [m.group() for m in re.finditer(r"[^。！？!?]+[。！？!?]*[”’」』]*|[。！？!?]+", text) if m.group().strip()]
    # English abbreviation-aware behavior remains in the existing callers.
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def split_chinese(text: str, budget: int) -> list[str]:
    """Split at punctuation first, retaining every non-whitespace source character."""
    if budget < 1:
        raise ValueError("text budget must be positive")
    result = []
    current = ""
    for match in re.finditer(r"[^。！？!?；，]+[。！？!?；，]*[”’」』]*|[。！？!?；，]+", text):
        fragment = match.group()
        if unit_count(current + fragment) <= budget:
            current += fragment
            continue
        if current:
            result.append(current)
            current = ""
        spans = unit_spans(fragment)
        start = 0
        for i in range(budget, len(spans), budget):
            end = spans[i][0]
            result.append(fragment[start:end])
            start = end
        current = fragment[start:]
    if current:
        result.append(current)
    return result
