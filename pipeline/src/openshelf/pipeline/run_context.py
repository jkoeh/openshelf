"""Per-build run context for resumable pipeline runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUN_CONTEXT_VERSION = 1
_BUILD_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MATCH_FIELDS = (
    "version",
    "build",
    "pipeline_version",
    "epub_sha256",
    "author_slug",
    "title_slug",
    "engine",
    "voice",
    "rendition",
    "cast_mode",
    "performance_direction_mode",
    "language",
    "chapters",
)


class RunContextError(ValueError):
    """Raised when an existing run context cannot be reused safely."""


@dataclass(frozen=True)
class RunContext:
    build: str
    pipeline_version: str
    epub_path: str
    epub_sha256: str
    author_slug: str
    title_slug: str
    book_author: str
    book_title: str
    engine: str
    voice: str
    rendition: str
    cast_mode: str
    performance_direction_mode: str
    language: str
    chapters: list[int]
    version: int = RUN_CONTEXT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "build": self.build,
            "pipeline_version": self.pipeline_version,
            "epub_path": self.epub_path,
            "epub_sha256": self.epub_sha256,
            "author_slug": self.author_slug,
            "title_slug": self.title_slug,
            "book_author": self.book_author,
            "book_title": self.book_title,
            "engine": self.engine,
            "voice": self.voice,
            "rendition": self.rendition,
            "cast_mode": self.cast_mode,
            "performance_direction_mode": self.performance_direction_mode,
            "language": self.language,
            "chapters": list(self.chapters),
        }


def validate_build_id(build_id: str) -> str:
    normalized = build_id.strip().lower()
    if not _BUILD_ID_RE.fullmatch(normalized):
        raise RunContextError("build_id must be exactly 16 lowercase hex characters")
    return normalized


def file_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_context_path(build_dir: str | os.PathLike[str]) -> str:
    return str(Path(build_dir) / "run.json")


def make_run_context(
    *,
    build: str,
    pipeline_version: str,
    epub_path: str,
    author_slug: str,
    title_slug: str,
    book_author: str,
    book_title: str,
    engine: str,
    voice: str,
    rendition: str,
    cast_mode: str,
    performance_direction_mode: str,
    language: str,
    chapters: list[int],
) -> RunContext:
    return RunContext(
        build=validate_build_id(build),
        pipeline_version=pipeline_version,
        epub_path=str(Path(epub_path).resolve()),
        epub_sha256=file_sha256(epub_path),
        author_slug=author_slug,
        title_slug=title_slug,
        book_author=book_author,
        book_title=book_title,
        engine=engine,
        voice=voice,
        rendition=rendition,
        cast_mode=cast_mode,
        performance_direction_mode=performance_direction_mode,
        language=language,
        chapters=sorted(chapters),
    )


def load_run_context(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_resume_context(existing: dict[str, Any], expected: RunContext) -> None:
    expected_dict = expected.to_dict()
    mismatches: list[str] = []
    for field in _MATCH_FIELDS:
        if existing.get(field) != expected_dict.get(field):
            mismatches.append(field)
    if mismatches:
        joined = ", ".join(mismatches)
        raise RunContextError(f"run context mismatch: {joined}")


def prepare_run_context(
    path: str | os.PathLike[str],
    expected: RunContext,
    *,
    resume: bool = False,
    force: bool = False,
) -> str:
    path_obj = Path(path)
    if path_obj.exists():
        if force:
            write_run_context(path_obj, expected)
            return str(path_obj)
        if not resume:
            raise RunContextError(
                f"run context already exists: {path_obj}; pass --resume or --force"
            )
        validate_resume_context(load_run_context(path_obj), expected)
        return str(path_obj)

    path_obj.parent.mkdir(parents=True, exist_ok=True)
    write_run_context(path_obj, expected)
    return str(path_obj)


def write_run_context(path: str | os.PathLike[str], context: RunContext) -> None:
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    with open(path_obj, "w", encoding="utf-8") as f:
        json.dump(context.to_dict(), f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
