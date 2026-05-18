"""Book manifest (Step 5a) and per-build rendition manifest (Step 5c).

The book manifest at `books/{author}/{title}/manifest.json` is the only mutable
per-book artifact on R2. It is a tiny pointer that names the `current_build` per
rendition. Always overwritten on upload.

The rendition manifest at `audio/{rendition}/builds/{build}/rendition-manifest.json`
is immutable per (rendition, build). Each build ID is minted fresh for a pipeline
run, so reruns publish under a new prefix instead of overwriting prior build bytes.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ChapterMeta:
    """Per-chapter metadata as produced by the pipeline orchestrator."""
    number: int
    title: str
    filename: str           # e.g. "chapter-01.m4a"
    duration_seconds: float
    word_count: int


@dataclass
class RenditionEntry:
    """One entry in the book manifest's `renditions` dict."""
    voice: str
    engine: str
    display: str
    current_build: str
    available_builds: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def generate_rendition_entry(
    voice: str,
    engine: str,
    display: str,
    build_id: str,
) -> RenditionEntry:
    """Build a RenditionEntry for a fresh build (single-entry available_builds)."""
    return RenditionEntry(
        voice=voice,
        engine=engine,
        display=display,
        current_build=build_id,
        available_builds=[build_id],
    )


# ---------------------------------------------------------------------------
# Book manifest (mutable per-book pointer)
# ---------------------------------------------------------------------------


def _rendition_entry_to_dict(entry: RenditionEntry) -> dict:
    return {
        "voice": entry.voice,
        "engine": entry.engine,
        "display": entry.display,
        "current_build": entry.current_build,
        "available_builds": list(entry.available_builds),
    }


def generate_book_manifest(
    author: str,
    title: str,
    source: str,
    renditions: dict[str, RenditionEntry],
    output_dir: str,
) -> str:
    """Write a fresh book manifest. Returns the path to manifest.json.

    Always overwrites — this is the mutable pointer file. Use `merge_book_manifest`
    first when a prior manifest exists, then write the merged dict (e.g. via
    json.dump) instead of calling this from scratch.
    """
    manifest = {
        "title": title,
        "author": author,
        "source": source,
        "renditions": {
            rendition: _rendition_entry_to_dict(entry)
            for rendition, entry in renditions.items()
        },
    }
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
    logger.info("Book manifest written: %s", path)
    return path


def merge_book_manifest(
    prior_manifest: dict,
    rendition: str,
    new_entry: RenditionEntry,
    retain: int = 2,
) -> dict:
    """Return a new manifest dict with `rendition` updated to `new_entry`.

    Semantics:
      - `new_entry.current_build` is prepended to `available_builds`.
      - Duplicate build IDs are removed (a no-op rerun does not grow the list).
      - The list is truncated to the most recent `retain` entries.
      - Other renditions in `prior_manifest` are preserved unchanged.
      - The caller's `prior_manifest` is not mutated.
    """
    merged = copy.deepcopy(prior_manifest)
    merged.setdefault("renditions", {})

    prior_entry = merged["renditions"].get(rendition, {})
    prior_builds = prior_entry.get("available_builds", []) if isinstance(prior_entry, dict) else []

    builds = [new_entry.current_build] + [b for b in prior_builds if b != new_entry.current_build]
    builds = builds[:retain]

    merged["renditions"][rendition] = {
        "voice": new_entry.voice,
        "engine": new_entry.engine,
        "display": new_entry.display,
        "current_build": new_entry.current_build,
        "available_builds": builds,
    }
    return merged


# ---------------------------------------------------------------------------
# Rendition manifest (per-build, immutable)
# ---------------------------------------------------------------------------


def _chapter_meta_to_dict(chapter: ChapterMeta) -> dict:
    return {
        "number": chapter.number,
        "title": chapter.title,
        "filename": chapter.filename,
        "duration_seconds": chapter.duration_seconds,
        "word_count": chapter.word_count,
    }


def generate_rendition_manifest(
    rendition: str,
    build_id: str,
    voice: str,
    engine: str,
    pipeline_version: str,
    chapters: list[ChapterMeta],
    output_dir: str,
) -> str:
    """Write a rendition-manifest.json for one (rendition, build). Returns the path.

    The manifest is written under a fresh per-run build prefix. It intentionally
    contains no wall-clock fields so the artifact describes only the build output.
    """
    manifest = {
        "build": build_id,
        "rendition": rendition,
        "voice": voice,
        "engine": engine,
        "pipeline_version": pipeline_version,
        "total_duration_seconds": sum(ch.duration_seconds for ch in chapters),
        "chapters": [_chapter_meta_to_dict(ch) for ch in chapters],
    }
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "rendition-manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
    logger.info("Rendition manifest written: %s", path)
    return path
