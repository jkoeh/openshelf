"""Factories for TTS engines and aligners."""

from __future__ import annotations

from openshelf.pipeline.tts_engine import NullAligner, TTSEngine, WordAligner


def create_engine(name: str | None = None, **kwargs) -> TTSEngine:
    selected = (name or "kokoro").lower().replace("-", "").replace("_", "")
    if selected == "kokoro":
        from openshelf.pipeline.engines.kokoro import KokoroAdapter

        return KokoroAdapter(**kwargs)
    if selected in {"f5tts", "f5"}:
        from openshelf.pipeline.engines.f5tts import F5TTSAdapter

        return F5TTSAdapter(**kwargs)
    if selected in {"chatterbox", "chatterboxtts"}:
        from openshelf.pipeline.engines.chatterbox import ChatterboxAdapter

        return ChatterboxAdapter(**kwargs)
    raise ValueError(f"Unsupported TTS engine: {name}")


def create_aligner(engine: TTSEngine, device: str = "cpu") -> WordAligner:
    if engine.post_processing_config().needs_forced_alignment:
        try:
            from openshelf.pipeline.word_aligner_protocol import WhisperXAligner
        except ImportError:
            return NullAligner()
        return WhisperXAligner(device=device)
    return NullAligner()
