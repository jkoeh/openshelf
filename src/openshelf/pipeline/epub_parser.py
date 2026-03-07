"""Step 1: Parse EPUB files into chapters of clean plain text."""

from dataclasses import dataclass


@dataclass
class Chapter:
    number: int
    title: str
    text: str
    word_count: int
