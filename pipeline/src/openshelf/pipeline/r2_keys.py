"""R2 key constructors for the build-versioned layout.

Every R2 key in the pipeline goes through this module. See
pipeline/docs/step6-r2.md for the layout these keys describe, and
worker/src/utils/r2-keys.ts for the TypeScript mirror that the worker
uses to read the same keys — their tests assert identical strings.

All inputs are expected to already be sanitized slugs
(see openshelf/scrapers/http.py:sanitize). This module performs no
sanitization, it only joins strings.
"""

from __future__ import annotations

from openshelf.config import R2_PREFIX_BOOKS


def book_prefix(author: str, title: str) -> str:
    """The shared per-book R2 prefix: `books/{author}/{title}`."""
    return f"{R2_PREFIX_BOOKS}/{author}/{title}"


def book_manifest_key(author: str, title: str) -> str:
    """The single mutable per-book pointer file."""
    return f"{book_prefix(author, title)}/manifest.json"


def book_epub_key(author: str, title: str) -> str:
    """The annotated EPUB written by step1b."""
    return f"{book_prefix(author, title)}/book.epub"


def cover_key(author: str, title: str, content_type: str = "image/jpeg") -> str:
    """Cover image key. Extension is derived from `content_type` (jpg by default)."""
    ext = "png" if "png" in content_type else "jpg"
    return f"{book_prefix(author, title)}/cover.{ext}"


def build_prefix(author: str, title: str, rendition: str, build: str) -> str:
    """The per-(rendition, build) prefix that holds an atomic snapshot of output."""
    return f"{book_prefix(author, title)}/audio/{rendition}/builds/{build}"


def audio_key(author: str, title: str, rendition: str, build: str, chapter_number: int) -> str:
    """The m4a file for one chapter under a specific (rendition, build)."""
    return f"{build_prefix(author, title, rendition, build)}/chapter-{chapter_number:02d}.m4a"


def chapter_data_key(author: str, title: str, rendition: str, build: str) -> str:
    """The per-chunk text + word timestamps JSON for one (rendition, build)."""
    return f"{build_prefix(author, title, rendition, build)}/chapter_data.json"


def character_registry_key(author: str, title: str, rendition: str, build: str) -> str:
    """The narrator/character voice registry used by one (rendition, build)."""
    return f"{build_prefix(author, title, rendition, build)}/character_registry.json"


def voice_direction_key(author: str, title: str, rendition: str, build: str) -> str:
    """The speaker and performance-direction audit for one (rendition, build)."""
    return f"{build_prefix(author, title, rendition, build)}/voice_direction.json"


def run_context_key(author: str, title: str, rendition: str, build: str) -> str:
    """The run context used to validate local resume for one (rendition, build)."""
    return f"{build_prefix(author, title, rendition, build)}/run.json"


def rendition_manifest_key(author: str, title: str, rendition: str, build: str) -> str:
    """The per-build manifest describing chapter durations and word counts.

    Always written last in an upload so its presence on R2 signals the
    entire build is complete (single-gate idempotency).
    """
    return f"{build_prefix(author, title, rendition, build)}/rendition-manifest.json"
