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
    LEAD_IN_SILENCE_MS,
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


def _extract_words(results: list, sample_rate: int = TTS_SAMPLE_RATE) -> list[WordTimestamp]:
    """Extract word timestamps from Kokoro Result objects (chunk-relative).

    Misaki MTokens are sub-word units (a contraction like "don't" can split
    into several tokens). The token's `whitespace` attribute holds the
    whitespace that follows it, so a non-empty `whitespace` marks a word
    boundary. We accumulate consecutive tokens into a single word, taking
    `start_ts` from the first token in the run and `end_ts` from the last.

    A single `pipeline(text)` call can yield multiple `Result` objects when
    Kokoro segments the input internally. Token timestamps are reported
    relative to *each Result's own audio*, so we accumulate audio durations
    across Results and add the running offset to every token timestamp.
    """
    words: list[WordTimestamp] = []
    offset = 0.0  # seconds of audio from prior Results in this synth call

    def _flush(buf: list, off: float) -> None:
        if not buf:
            return
        text = "".join(getattr(t, "text", "") for t in buf).strip()
        if not text:
            return
        words.append(WordTimestamp(
            word=text,
            start=round(buf[0].start_ts + off, 4),
            end=round(buf[-1].end_ts + off, 4),
        ))

    for r in results:
        tokens = getattr(r, "tokens", None) or []
        buf: list = []
        for tok in tokens:
            text = getattr(tok, "text", "")
            start_ts = getattr(tok, "start_ts", None)
            end_ts = getattr(tok, "end_ts", None)
            whitespace = getattr(tok, "whitespace", "")

            if text and not text.isspace() and start_ts is not None and end_ts is not None:
                buf.append(tok)

            if whitespace:
                _flush(buf, offset)
                buf = []
        _flush(buf, offset)

        # Advance the running offset by this Result's audio duration so the
        # next Result's tokens land on the correct global timeline.
        audio_attr = r.audio if hasattr(r, "audio") else (r[2] if len(r) > 2 else None)
        if audio_attr is not None:
            arr = np.asarray(audio_attr, dtype=np.float32)
            offset += len(arr) / sample_rate

    return words


def _synthesize_single_chunk(
    pipeline: Any,
    chunk_info: ChunkInfo,
    voice: str,
    sample_rate: int,
) -> tuple[np.ndarray, list[WordTimestamp]]:
    """Synthesize a single chunk. Returns (audio, word_timestamps)."""
    results = list(pipeline(chunk_info.text, voice=voice))
    if not results:
        raise RuntimeError("No audio returned")
    chunk_audio = np.concatenate([np.asarray(
        r.audio if hasattr(r, "audio") else r[2], dtype=np.float32
    ) for r in results])
    words = _extract_words(results)
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

    # Lead-in silence absorbs the AAC encoder priming samples and Kokoro's
    # first-token onset transient so the file doesn't start with a click.
    lead_in = _generate_silence(sample_rate, LEAD_IN_SILENCE_MS)
    audio_segments: list[np.ndarray] = [lead_in]
    skipped = 0
    frames_so_far = len(lead_in)
    chunk_audio_starts: list[float] = []
    chunk_words: list[list[WordTimestamp]] = []
    prior_chunk_emitted = False

    for i, chunk_info in enumerate(chunks):
        try:
            chunk_audio, words = _synthesize_single_chunk(pipeline, chunk_info, voice, sample_rate)

            if prior_chunk_emitted:
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
            prior_chunk_emitted = True
        except Exception:
            logger.warning("Chunk %d failed, skipping: %.40s...", i, chunk_info.text, exc_info=True)
            chunk_audio_starts.append(-1.0)
            chunk_words.append([])
            skipped += 1

    if not prior_chunk_emitted:
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
