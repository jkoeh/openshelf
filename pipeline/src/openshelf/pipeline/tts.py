"""Step 3: Generate audio from text chunks using Kokoro TTS."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import soundfile as sf

from openshelf.config import (
    TTS_VOICE,
    TTS_LANGUAGE,
    TTS_SAMPLE_RATE,
    SILENCE_PARAGRAPH_BREAK_MS,
    SILENCE_MID_PARAGRAPH_MS,
    CROSSFADE_MS,
)

logger = logging.getLogger(__name__)

# Lazy-loaded at first use — torch and kokoro are heavy deps
torch: Any = None
KPipeline: Any = None


def _import_torch() -> Any:
    global torch  # noqa: PLW0603
    if torch is None:
        import torch as _torch
        torch = _torch
    return torch


def _import_kpipeline() -> Any:
    global KPipeline  # noqa: PLW0603
    if KPipeline is None:
        from kokoro import KPipeline as _KP
        KPipeline = _KP
    return KPipeline


@dataclass
class ChunkInfo:
    text: str
    ends_paragraph: bool = True
    context_prefix: str = ""


@dataclass
class ChapterAudio:
    chapter_number: int
    title: str
    wav_path: str
    duration_seconds: float
    word_count: int


@dataclass
class SynthesisResult:
    duration_seconds: float
    skipped_chunks: int
    chunk_audio_starts: list[float]   # len == len(input chunks); -1.0 for skipped chunks


def get_device() -> str:
    t = _import_torch()
    if t.cuda.is_available():
        return "cuda"
    if t.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_pipeline(device: str | None = None):
    kp = _import_kpipeline()
    if device is None:
        device = get_device()
    logger.info("Loading Kokoro pipeline on %s", device)
    return kp(lang_code=TTS_LANGUAGE, device=device)


def _generate_silence(sample_rate: int, duration_ms: int) -> np.ndarray:
    n_samples = int(sample_rate * duration_ms / 1000)
    return np.zeros(n_samples, dtype=np.float32)


def _normalize(audio: np.ndarray, target_peak: float = 0.89) -> np.ndarray:
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio * (target_peak / peak)
    return audio


def _apply_boundary_fades(
    audio: np.ndarray, sample_rate: int, fade_ms: int = CROSSFADE_MS
) -> np.ndarray:
    """Apply fade-in at start and fade-out at end to smooth chunk boundaries."""
    fade_samples = int(sample_rate * fade_ms / 1000)
    if len(audio) < 2 * fade_samples:
        return audio
    audio = audio.copy()
    audio[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)
    audio[-fade_samples:] *= np.linspace(1, 0, fade_samples, dtype=np.float32)
    return audio


def _split_segments_at_prefix(
    results: list[tuple], context_prefix: str, sample_rate: int,
    fade_ms: int = CROSSFADE_MS,
) -> np.ndarray:
    """Use Kokoro segment graphemes to find where prefix ends, return only real-content audio.

    Each result tuple is (graphemes, phonemes, audio_array).  We accumulate
    grapheme text until it covers the context_prefix, then keep audio from
    the remaining segments only.
    """
    prefix_clean = _text_for_matching(context_prefix)
    prefix_len = len(prefix_clean)

    if prefix_len == 0:
        return np.concatenate([r[2] for r in results])

    accumulated = 0
    split_idx = 0  # first segment that belongs to real content

    for idx, (graphemes, _phonemes, _audio) in enumerate(results):
        seg_text = _text_for_matching(graphemes)
        accumulated += len(seg_text)
        if accumulated >= prefix_len:
            split_idx = idx + 1
            break

    # If all segments were prefix (shouldn't happen), fall back to proportional
    if split_idx >= len(results):
        prefix_words = len(context_prefix.split())
        total_words = sum(len(r[0].split()) for r in results)
        full_audio = np.concatenate([r[2] for r in results])
        if total_words > 0 and prefix_words < total_words:
            trim_samples = int(len(full_audio) * prefix_words / total_words)
            trimmed = full_audio[trim_samples:]
        else:
            trimmed = full_audio
        fade_samples = min(int(sample_rate * fade_ms / 1000), len(trimmed))
        if fade_samples > 0:
            trimmed = trimmed.copy()
            trimmed[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)
        return trimmed

    # Keep only segments after the prefix
    real_audio = np.concatenate([r[2] for r in results[split_idx:]])

    # Fade-in at the start to smooth the splice
    fade_samples = min(int(sample_rate * fade_ms / 1000), len(real_audio))
    if fade_samples > 0:
        real_audio = real_audio.copy()
        real_audio[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)

    return real_audio


def _text_for_matching(text: str) -> str:
    """Normalize text for prefix matching — lowercase, collapse whitespace, strip punctuation."""
    import re
    return re.sub(r"[^a-z0-9]", "", text.lower())


def synthesize_chapter(
    pipeline: Any,
    chunks: list[ChunkInfo],
    output_path: str,
    voice: str = TTS_VOICE,
    sample_rate: int = TTS_SAMPLE_RATE,
) -> SynthesisResult:
    if not chunks:
        raise ValueError("chunks list is empty")

    audio_segments: list[np.ndarray] = []
    skipped = 0
    frames_so_far = 0
    chunk_audio_starts: list[float] = []

    for i, chunk_info in enumerate(chunks):
        try:
            # Build synthesis text with optional context prefix
            if chunk_info.context_prefix:
                synth_text = chunk_info.context_prefix + " " + chunk_info.text
            else:
                synth_text = chunk_info.text

            results = list(pipeline(synth_text, voice=voice))
            if not results:
                raise RuntimeError(f"No audio returned for chunk {i}")

            # Trim context prefix audio using segment boundaries
            if chunk_info.context_prefix:
                chunk_audio = _split_segments_at_prefix(
                    results, chunk_info.context_prefix, sample_rate
                )
            else:
                chunk_audio = np.concatenate([r[2] for r in results])

            chunk_audio = _normalize(chunk_audio)
            chunk_audio = _apply_boundary_fades(chunk_audio, sample_rate)

            if audio_segments:
                # Variable silence: longer at paragraph breaks, shorter mid-paragraph
                if chunks[i - 1].ends_paragraph:
                    gap_ms = SILENCE_PARAGRAPH_BREAK_MS
                else:
                    gap_ms = SILENCE_MID_PARAGRAPH_MS
                silence = _generate_silence(sample_rate, gap_ms)
                frames_so_far += len(silence)
                audio_segments.append(silence)

            chunk_audio_starts.append(frames_so_far / sample_rate)
            audio_segments.append(chunk_audio)
            frames_so_far += len(chunk_audio)
        except Exception:
            logger.warning("Chunk %d failed, skipping: %.40s...", i, chunk_info.text)
            chunk_audio_starts.append(-1.0)
            skipped += 1

    if not audio_segments:
        raise RuntimeError("All chunks failed TTS synthesis")

    full_audio = np.concatenate(audio_segments)
    sf.write(output_path, full_audio, sample_rate)

    duration = len(full_audio) / sample_rate
    return SynthesisResult(
        duration_seconds=duration,
        skipped_chunks=skipped,
        chunk_audio_starts=chunk_audio_starts,
    )
