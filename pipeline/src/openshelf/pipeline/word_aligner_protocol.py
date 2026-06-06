"""WordAligner implementations for TTSEngine adapters."""

from __future__ import annotations

import logging
import os
import tempfile

import numpy as np

from openshelf.pipeline.tts_engine import WordTimestamp

logger = logging.getLogger(__name__)


class WhisperXAligner:
    """Align one synthesized segment and return segment-relative timestamps."""
    supports_context_trim = True

    def __init__(self, device: str = "cpu", language: str = "en"):
        self.device = device
        self.language = language

    def align(
        self,
        audio: np.ndarray,
        text: str,
        sample_rate: int,
    ) -> list[WordTimestamp]:
        return self.align_segments(audio, [text], [0.0], sample_rate)

    def align_segments(
        self,
        audio: np.ndarray,
        texts: list[str],
        starts: list[float],
        sample_rate: int,
    ) -> list[WordTimestamp]:
        tmp_path = ""
        try:
            import soundfile as sf
            from openshelf.pipeline.word_aligner import align_chapter

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            sf.write(tmp_path, audio, sample_rate)
            entries = align_chapter(
                tmp_path,
                texts,
                starts,
                device=self.device,
                language=self.language,
            )
            return [
                WordTimestamp(
                    word=entry.word,
                    start=entry.start,
                    end=entry.end,
                )
                for entry in entries
            ]
        except Exception:
            logger.warning("WhisperX forced alignment failed", exc_info=True)
            return []
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
