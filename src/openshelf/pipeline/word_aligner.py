"""Step 5b: Word-level forced alignment via WhisperX."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WordEntry:
    word: str
    start: float
    end: float
    chunk_idx: int  # index into the chapter's chunk list


def _next_start(chunk_audio_starts: list[float], current_idx: int) -> float:
    """Return the start time of the next non-skipped chunk after current_idx."""
    for i in range(current_idx + 1, len(chunk_audio_starts)):
        if chunk_audio_starts[i] >= 0.0:
            return chunk_audio_starts[i]
    return float("inf")


def align_chapter(
    audio_path: str,
    chunk_texts: list[str],
    chunk_audio_starts: list[float],
    device: str = "cpu",
    language: str = "en",
) -> list[WordEntry]:
    """Run WhisperX forced alignment on a chapter audio file.

    Args:
        audio_path: path to the Opus (or WAV) file
        chunk_texts: text of each chunk in order
        chunk_audio_starts: start time (seconds) of each chunk; -1.0 = skipped
        device: "cpu" or "cuda"
        language: ISO 639-1 language code

    Returns:
        List of WordEntry with word timestamps and chunk_idx.
    """
    import whisperx  # lazy import — heavy dep

    # Build segments from known chunk start times (skip failed chunks)
    segments = []
    chunk_idx_map: list[int] = []  # segment index → original chunk index
    for i, (text, start_s) in enumerate(zip(chunk_texts, chunk_audio_starts)):
        if start_s < 0.0:
            continue
        end_s = _next_start(chunk_audio_starts, i)
        segments.append({"text": text, "start": start_s, "end": end_s})
        chunk_idx_map.append(i)

    if not segments:
        return []

    audio = whisperx.load_audio(audio_path)
    model_a, metadata = whisperx.load_align_model(language_code=language, device=device)
    result = whisperx.align(segments, model_a, metadata, audio, device)

    # Map words back to chunk_idx using segment boundaries
    seg_boundaries = [(seg["start"], seg["end"], chunk_idx_map[j])
                      for j, seg in enumerate(segments)]

    words: list[WordEntry] = []
    for w in result.get("word_segments", []):
        word_start = w.get("start", 0.0)
        # Find which segment this word falls in
        chunk_idx = chunk_idx_map[-1]  # default to last chunk
        for seg_start, seg_end, cidx in seg_boundaries:
            if seg_start <= word_start < seg_end:
                chunk_idx = cidx
                break
        words.append(WordEntry(
            word=w["word"],
            start=round(word_start, 4),
            end=round(w.get("end", word_start), 4),
            chunk_idx=chunk_idx,
        ))

    return words


def write_word_alignment(
    chapters: list[dict],
    rendition: str,
    output_path: str,
) -> str:
    """Write word alignment data to JSON. Idempotent: skips if file exists.

    Args:
        chapters: list of {"number": int, "words": list[WordEntry]}
        rendition: rendition identifier
        output_path: destination path

    Returns:
        output_path
    """
    if os.path.exists(output_path):
        logger.info("Word alignment already exists, skipping: %s", output_path)
        return output_path

    data = {
        "version": 1,
        "rendition": rendition,
        "chapters": [
            {
                "number": ch["number"],
                "words": [dataclasses.asdict(w) for w in ch["words"]],
            }
            for ch in chapters
        ],
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Word alignment written: %s", output_path)
    return output_path
