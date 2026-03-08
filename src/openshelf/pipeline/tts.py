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
    SILENCE_BETWEEN_CHUNKS_MS,
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


def synthesize_chapter(
    pipeline: Any,
    chunks: list[str],
    output_path: str,
    voice: str = TTS_VOICE,
    sample_rate: int = TTS_SAMPLE_RATE,
    silence_ms: int = SILENCE_BETWEEN_CHUNKS_MS,
) -> SynthesisResult:
    if not chunks:
        raise ValueError("chunks list is empty")

    silence = _generate_silence(sample_rate, silence_ms)
    audio_segments: list[np.ndarray] = []
    skipped = 0

    for i, chunk in enumerate(chunks):
        try:
            results = list(pipeline(chunk, voice=voice))
            if not results:
                raise RuntimeError(f"No audio returned for chunk {i}")
            chunk_audio = np.concatenate([r[2] for r in results])
            chunk_audio = _normalize(chunk_audio)

            if audio_segments:
                audio_segments.append(silence)
            audio_segments.append(chunk_audio)
        except Exception:
            logger.warning("Chunk %d failed, skipping: %.40s...", i, chunk)
            skipped += 1

    if not audio_segments:
        raise RuntimeError("All chunks failed TTS synthesis")

    full_audio = np.concatenate(audio_segments)
    sf.write(output_path, full_audio, sample_rate)

    duration = len(full_audio) / sample_rate
    return SynthesisResult(duration_seconds=duration, skipped_chunks=skipped)
