"""Mint or validate a build identifier for one pipeline run.

A "build" is the immutable R2 prefix the pipeline writes audio + chapter_data +
rendition-manifest into for a single (rendition, run). Default regenerations
get a brand-new random ID and prefix. Resumable runs may provide an explicit
16-hex ID; `run.json` validates that the selected prefix belongs to the same
EPUB and immutable invocation settings. The book-level manifest (mutable) is
the single pointer that names the `current_build` per rendition.

See pipeline/docs/step6-r2.md and the rendition-vs-build section in the root
CLAUDE.md for the contract.
"""

from __future__ import annotations

import secrets


_HEX_BYTES = 8  # secrets.token_hex(8) → 16 hex chars


def new_build_id() -> str:
    """Return a fresh random 16-hex-character build identifier."""
    return secrets.token_hex(_HEX_BYTES)
