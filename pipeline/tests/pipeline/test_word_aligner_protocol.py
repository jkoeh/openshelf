"""Tests for WordAligner wrappers."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.tts_engine import NullAligner  # noqa: E402
from openshelf.pipeline.engines import create_aligner  # noqa: E402
from openshelf.pipeline.engines.kokoro import KokoroAdapter  # noqa: E402
from openshelf.pipeline.word_aligner import WordEntry  # noqa: E402
from openshelf.pipeline.word_aligner_protocol import WhisperXAligner  # noqa: E402


class TestWhisperXAligner(unittest.TestCase):
    @patch("soundfile.write")
    @patch("openshelf.pipeline.word_aligner.align_chapter")
    def test_type_conversion(self, mock_align_chapter, mock_sf_write):
        mock_align_chapter.return_value = [
            WordEntry("Hello", 0.0, 0.3, 0),
            WordEntry("world", 0.3, 0.6, 0),
        ]

        words = WhisperXAligner().align(
            np.zeros(100, dtype=np.float32),
            "Hello world",
            24000,
        )

        self.assertEqual([w.word for w in words], ["Hello", "world"])
        self.assertEqual(words[0].start, 0.0)
        self.assertEqual(words[1].end, 0.6)
        mock_sf_write.assert_called_once()

    @patch("soundfile.write")
    @patch("openshelf.pipeline.word_aligner.align_chapter", side_effect=RuntimeError("boom"))
    def test_failure_returns_empty(self, mock_align_chapter, mock_sf_write):
        words = WhisperXAligner().align(
            np.zeros(100, dtype=np.float32),
            "Hello world",
            24000,
        )

        self.assertEqual(words, [])

    def test_null_aligner_returns_empty(self):
        self.assertEqual(
            NullAligner().align(np.zeros(10, dtype=np.float32), "text", 24000),
            [],
        )

    def test_factory_passes_device_to_whisperx_aligner(self):
        aligner = create_aligner(KokoroAdapter(), device="cuda")

        self.assertIsInstance(aligner, WhisperXAligner)
        self.assertEqual(aligner.device, "cuda")


if __name__ == "__main__":
    unittest.main()
