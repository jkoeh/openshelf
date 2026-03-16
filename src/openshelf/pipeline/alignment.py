"""Step 5b: Generate alignment.json mapping chunk indices to audio time offsets."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def build_alignment(
    rendition: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build alignment data from per-chapter synthesis results.

    Args:
        rendition: rendition identifier, e.g. "kokoro-af-heart"
        chapters: list of dicts, each with:
            "number": int
            "total_duration_s": float
            "chunk_audio_starts": list[float]  (-1.0 = skipped chunk)

    Returns:
        alignment dict matching the v1 schema. Skipped chunks (-1.0) are
        excluded from output — chunk_idx may have gaps. Frontend must
        degrade gracefully (disable paragraph-seek for missing chunks).
    """
    chapter_entries = []
    for ch in chapters:
        chunk_entries = [
            {"chunk_idx": i, "audio_start_s": round(t, 4)}
            for i, t in enumerate(ch["chunk_audio_starts"])
            if t >= 0.0
        ]
        chapter_entries.append({
            "number": ch["number"],
            "total_duration_s": round(ch["total_duration_s"], 4),
            "chunks": chunk_entries,
        })
    return {
        "version": 1,
        "rendition": rendition,
        "chapters": chapter_entries,
    }


def write_alignment(
    alignment: dict[str, Any],
    output_path: str,
) -> str:
    """Write alignment dict to JSON. Idempotent: skips if file exists.

    Returns the output_path.
    """
    if os.path.exists(output_path):
        logger.info("Alignment already exists, skipping: %s", output_path)
        return output_path

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(alignment, f, indent=2, ensure_ascii=False)
    logger.info("Alignment written: %s", output_path)
    return output_path
