"""Roundtrip transcription and WER validation for TTS output."""

from __future__ import annotations

import logging
import string
from typing import Any

from openshelf.config import WHISPER_MODEL_SIZE

logger = logging.getLogger(__name__)

# Lazy-loaded ASR model (heavy dep)
_asr_model: Any = None
_asr_device: str | None = None


def _load_asr_model(device: str = "cpu", language: str = "en") -> Any:
    global _asr_model, _asr_device  # noqa: PLW0603
    if _asr_model is not None and _asr_device == device:
        return _asr_model

    import whisperx

    compute_type = "float32" if device == "cpu" else "float16"
    _asr_model = whisperx.load_model(
        WHISPER_MODEL_SIZE, device, language=language, compute_type=compute_type
    )
    _asr_device = device
    return _asr_model


def transcribe_audio(
    audio_path: str,
    device: str = "cpu",
    language: str = "en",
) -> str:
    """Transcribe an audio file to text using WhisperX ASR.

    Returns the raw transcription as a single string.
    Returns empty string on failure (graceful degradation).
    """
    try:
        import whisperx

        model = _load_asr_model(device, language)
        audio = whisperx.load_audio(audio_path)
        result = model.transcribe(audio)

        segments = result.get("segments", [])
        return " ".join(seg.get("text", "").strip() for seg in segments).strip()
    except Exception:
        logger.exception("Transcription failed for %s", audio_path)
        return ""


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute Word Error Rate between reference and hypothesis texts.

    Applies standard normalizations: lowercase, remove punctuation, collapse whitespace.
    Returns WER as a float (0.0 = perfect match, 1.0 = completely wrong).
    Returns 1.0 if reference is empty (no words to compare against).
    Returns 0.0 if both are empty.
    """
    if not reference.strip() and not hypothesis.strip():
        return 0.0
    if not reference.strip():
        return 1.0

    try:
        import jiwer
    except ImportError:
        return _compute_wer_fallback(reference, hypothesis)

    transforms = jiwer.Compose([
        jiwer.ToLowerCase(),
        jiwer.RemovePunctuation(),
        jiwer.Strip(),
        jiwer.ReduceToSingleSentence(),
        jiwer.ReduceToListOfListOfWords(),
    ])

    return jiwer.wer(
        reference,
        hypothesis,
        reference_transform=transforms,
        hypothesis_transform=transforms,
    )


def _normalize_words(text: str) -> list[str]:
    table = str.maketrans("", "", string.punctuation)
    return text.lower().translate(table).split()


def _compute_wer_fallback(reference: str, hypothesis: str) -> float:
    ref_words = _normalize_words(reference)
    hyp_words = _normalize_words(hypothesis)
    if not ref_words and not hyp_words:
        return 0.0
    if not ref_words:
        return 1.0

    prev = list(range(len(hyp_words) + 1))
    for i, ref_word in enumerate(ref_words, start=1):
        curr = [i]
        for j, hyp_word in enumerate(hyp_words, start=1):
            cost = 0 if ref_word == hyp_word else 1
            curr.append(min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + cost,
            ))
        prev = curr
    return prev[-1] / len(ref_words)
