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
class WordTimestamp:
    word: str
    start: float  # seconds, relative to start of final chapter audio
    end: float


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
    chunk_words: list[list[WordTimestamp]]  # per-chunk word timestamps (empty list for skipped)


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
    if len(audio) == 0:
        return audio
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
    results: list, context_prefix: str, sample_rate: int,
    fade_ms: int = CROSSFADE_MS,
) -> tuple[np.ndarray, int]:
    """Trim prefix audio from Kokoro results. Returns (audio, trim_samples).

    Each result has .graphemes and audio (index 2 or .audio).
    trim_samples = total samples removed from the front (for timestamp adjustment).
    """
    def _get_graphemes(r: Any) -> str:
        return r.graphemes if hasattr(r, "graphemes") else r[0]

    def _get_audio(r: Any) -> np.ndarray:
        raw = r.audio if hasattr(r, "audio") else r[2]
        return np.asarray(raw, dtype=np.float32)

    prefix_clean = _text_for_matching(context_prefix)
    prefix_len = len(prefix_clean)

    if prefix_len == 0:
        return np.concatenate([_get_audio(r) for r in results]), 0

    # Count samples in segments before the straddle point
    accumulated = 0
    straddle_idx = -1
    samples_before_straddle = 0

    for idx, r in enumerate(results):
        seg_text = _text_for_matching(_get_graphemes(r))
        prev_accumulated = accumulated
        accumulated += len(seg_text)
        if accumulated >= prefix_len:
            straddle_idx = idx
            prefix_chars_in_seg = prefix_len - prev_accumulated
            seg_chars = len(seg_text)
            break
        samples_before_straddle += len(_get_audio(r))

    # All segments were prefix — fall back to proportional
    if straddle_idx == -1 or (straddle_idx == len(results) - 1 and prefix_chars_in_seg >= len(_text_for_matching(_get_graphemes(results[straddle_idx])))):
        prefix_words = len(context_prefix.split())
        total_words = sum(len(_get_graphemes(r).split()) for r in results)
        full_audio = np.concatenate([_get_audio(r) for r in results])
        if total_words > 0 and prefix_words < total_words:
            trim_samples = int(len(full_audio) * prefix_words / total_words)
            trimmed = full_audio[trim_samples:]
        else:
            trim_samples = 0
            trimmed = full_audio
        fade_samples = min(int(sample_rate * fade_ms / 1000), len(trimmed))
        if fade_samples > 0:
            trimmed = trimmed.copy()
            trimmed[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)
        return trimmed, trim_samples

    # Split the straddling segment proportionally
    parts: list[np.ndarray] = []
    straddle_trim = 0

    if seg_chars > 0 and prefix_chars_in_seg < seg_chars:
        straddle_audio = _get_audio(results[straddle_idx])
        content_fraction = 1.0 - (prefix_chars_in_seg / seg_chars)
        straddle_trim = int(len(straddle_audio) * (1.0 - content_fraction))
        content_audio = straddle_audio[straddle_trim:].copy()
        if len(content_audio) > 0:
            fade_samples = min(int(sample_rate * fade_ms / 1000), len(content_audio))
            if fade_samples > 0:
                content_audio[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)
            parts.append(content_audio)
    else:
        straddle_trim = len(_get_audio(results[straddle_idx]))

    for r in results[straddle_idx + 1:]:
        parts.append(_get_audio(r))

    total_trim = samples_before_straddle + straddle_trim

    if not parts:
        return np.zeros(0, dtype=np.float32), total_trim

    real_audio = np.concatenate(parts)

    if prefix_chars_in_seg >= seg_chars:
        fade_samples = min(int(sample_rate * fade_ms / 1000), len(real_audio))
        if fade_samples > 0:
            real_audio = real_audio.copy()
            real_audio[:fade_samples] *= np.linspace(0, 1, fade_samples, dtype=np.float32)

    return real_audio, total_trim


def _text_for_matching(text: str) -> str:
    """Normalize text for prefix matching — lowercase, collapse whitespace, strip punctuation."""
    import re
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _extract_words(
    results: list,
    trim_offset: float,
) -> list[WordTimestamp]:
    """Extract word timestamps from Kokoro Result objects, adjusting for prefix trim.

    Filters out tokens with no timestamps (punctuation, whitespace) and tokens
    that fall within the trimmed prefix region.
    """
    words: list[WordTimestamp] = []
    for r in results:
        tokens = getattr(r, "tokens", None)
        if not tokens:
            continue
        for tok in tokens:
            start_ts = getattr(tok, "start_ts", None)
            end_ts = getattr(tok, "end_ts", None)
            text = getattr(tok, "text", "")
            if start_ts is None or end_ts is None or not text.strip():
                continue
            # Skip tokens that fall within the trimmed prefix
            if start_ts < trim_offset - 0.01:
                continue
            words.append(WordTimestamp(
                word=text.strip(),
                start=round(start_ts - trim_offset, 4),
                end=round(end_ts - trim_offset, 4),
            ))
    return words


def _synthesize_single_chunk(
    pipeline: Any,
    chunk_info: ChunkInfo,
    voice: str,
    sample_rate: int,
) -> tuple[np.ndarray, list[WordTimestamp]]:
    """Synthesize a single chunk. Returns (audio, word_timestamps).

    Retries without context prefix on failure.
    """
    if chunk_info.context_prefix:
        synth_text = chunk_info.context_prefix + " " + chunk_info.text
        try:
            results = list(pipeline(synth_text, voice=voice))
            if not results:
                raise RuntimeError("No audio returned")
            chunk_audio, trim_samples = _split_segments_at_prefix(
                results, chunk_info.context_prefix, sample_rate
            )
            trim_offset = trim_samples / sample_rate
            words = _extract_words(results, trim_offset)
            chunk_audio = _normalize(chunk_audio)
            return _apply_boundary_fades(chunk_audio, sample_rate), words
        except Exception:
            logger.info("Retrying chunk without context prefix: %.40s...", chunk_info.text)

    # No prefix, or prefix attempt failed — synthesize plain text
    results = list(pipeline(chunk_info.text, voice=voice))
    if not results:
        raise RuntimeError("No audio returned")
    chunk_audio = np.concatenate([np.asarray(
        r.audio if hasattr(r, "audio") else r[2], dtype=np.float32
    ) for r in results])
    words = _extract_words(results, 0.0)
    chunk_audio = _normalize(chunk_audio)
    return _apply_boundary_fades(chunk_audio, sample_rate), words


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
    chunk_words: list[list[WordTimestamp]] = []

    for i, chunk_info in enumerate(chunks):
        try:
            chunk_audio, words = _synthesize_single_chunk(pipeline, chunk_info, voice, sample_rate)

            if audio_segments:
                # Variable silence: longer at paragraph breaks, shorter mid-paragraph
                if chunks[i - 1].ends_paragraph:
                    gap_ms = SILENCE_PARAGRAPH_BREAK_MS
                else:
                    gap_ms = SILENCE_MID_PARAGRAPH_MS
                silence = _generate_silence(sample_rate, gap_ms)
                frames_so_far += len(silence)
                audio_segments.append(silence)

            chunk_start = frames_so_far / sample_rate
            chunk_audio_starts.append(chunk_start)

            # Offset word timestamps to be relative to the full chapter audio
            offset_words = [
                WordTimestamp(
                    word=w.word,
                    start=round(w.start + chunk_start, 4),
                    end=round(w.end + chunk_start, 4),
                )
                for w in words
            ]
            chunk_words.append(offset_words)

            audio_segments.append(chunk_audio)
            frames_so_far += len(chunk_audio)
        except Exception:
            logger.warning("Chunk %d failed, skipping: %.40s...", i, chunk_info.text, exc_info=True)
            chunk_audio_starts.append(-1.0)
            chunk_words.append([])
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
        chunk_words=chunk_words,
    )
