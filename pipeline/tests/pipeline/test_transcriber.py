"""Tests for transcriber.py — roundtrip WER validation."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.transcriber import compute_wer, transcribe_audio


class TestComputeWer(unittest.TestCase):

    def test_identical_texts(self):
        self.assertAlmostEqual(compute_wer("hello world", "hello world"), 0.0)

    def test_completely_different(self):
        self.assertAlmostEqual(compute_wer("hello world", "foo bar"), 1.0)

    def test_case_insensitive(self):
        self.assertAlmostEqual(compute_wer("Hello World", "hello world"), 0.0)

    def test_punctuation_ignored(self):
        self.assertAlmostEqual(compute_wer("Hello, world!", "hello world"), 0.0)

    def test_missing_words_detected(self):
        # 1 deletion out of 4 reference words = 0.25
        wer = compute_wer("the quick brown fox", "quick brown fox")
        self.assertAlmostEqual(wer, 0.25)

    def test_extra_words_detected(self):
        # 1 insertion out of 2 reference words = 0.5
        wer = compute_wer("brown fox", "the brown fox")
        self.assertAlmostEqual(wer, 0.5)

    def test_both_empty(self):
        self.assertAlmostEqual(compute_wer("", ""), 0.0)

    def test_empty_reference_nonempty_hypothesis(self):
        self.assertAlmostEqual(compute_wer("", "some words"), 1.0)

    def test_empty_hypothesis(self):
        # All words missing = 1.0
        self.assertAlmostEqual(compute_wer("hello world", ""), 1.0)

    def test_whitespace_only(self):
        self.assertAlmostEqual(compute_wer("   ", "   "), 0.0)

    def test_substitution(self):
        # 1 substitution out of 3 reference words = 1/3
        wer = compute_wer("the big dog", "the small dog")
        self.assertAlmostEqual(wer, 1.0 / 3.0, places=2)


class TestTranscribeAudio(unittest.TestCase):

    @patch("openshelf.pipeline.transcriber._load_asr_model")
    @patch("openshelf.pipeline.transcriber.whisperx", create=True)
    def test_returns_joined_segments(self, mock_whisperx, mock_load):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "segments": [
                {"text": "Hello world."},
                {"text": "This is a test."},
            ]
        }
        mock_load.return_value = mock_model
        mock_whisperx.load_audio.return_value = MagicMock()

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            result = transcribe_audio("/tmp/test.wav")

        self.assertEqual(result, "Hello world. This is a test.")

    @patch("openshelf.pipeline.transcriber._load_asr_model")
    @patch("openshelf.pipeline.transcriber.whisperx", create=True)
    def test_empty_segments(self, mock_whisperx, mock_load):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"segments": []}
        mock_load.return_value = mock_model
        mock_whisperx.load_audio.return_value = MagicMock()

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            result = transcribe_audio("/tmp/test.wav")

        self.assertEqual(result, "")

    @patch("openshelf.pipeline.transcriber._load_asr_model", side_effect=RuntimeError("no GPU"))
    def test_failure_returns_empty(self, mock_load):
        result = transcribe_audio("/tmp/test.wav")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
