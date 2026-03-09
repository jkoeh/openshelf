"""Step 5: Generate manifest.json for a processed book."""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ChapterMeta:
    number: int
    title: str
    filename: str           # e.g. "chapter-01.mp3"
    duration_seconds: float
    word_count: int


def generate_manifest(
    author: str,
    title: str,
    source: str,
    chapters: list[ChapterMeta],
    output_dir: str,
) -> str:
    """Write manifest.json for a processed book. Returns path to the manifest file."""
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, "manifest.json")

    if os.path.exists(manifest_path):
        logger.info("Manifest already exists, skipping: %s", manifest_path)
        return manifest_path

    manifest = {
        "title": title,
        "author": author,
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_duration_seconds": sum(ch.duration_seconds for ch in chapters),
        "chapters": [
            {
                "number": ch.number,
                "title": ch.title,
                "filename": ch.filename,
                "duration_seconds": ch.duration_seconds,
                "word_count": ch.word_count,
            }
            for ch in chapters
        ],
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info("Manifest written: %s", manifest_path)
    return manifest_path
