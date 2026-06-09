"""Tests for word_aligner.py — Step 5b of the pipeline."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.word_aligner import (
    WordEntry,
    align_chapter,
    validate_alignment,
    write_word_alignment,
    _next_start,
)


class TestNextStart(unittest.TestCase):

    def test_returns_next_non_skipped(self):
        starts = [0.0, -1.0, 5.0, 10.0]
        self.assertEqual(_next_start(starts, 0), 5.0)

    def test_skips_negative(self):
        starts = [0.0, -1.0, -1.0, 8.0]
        self.assertEqual(_next_start(starts, 0), 8.0)

    def test_returns_inf_when_no_more(self):
        starts = [0.0, 5.0]
        self.assertEqual(_next_start(starts, 1), float("inf"))

    def test_returns_inf_for_last(self):
        starts = [0.0]
        self.assertEqual(_next_start(starts, 0), float("inf"))


class TestValidateAlignment(unittest.TestCase):

    def test_valid_alignment_no_violations(self):
        words = [
            WordEntry("Hello", 0.1, 0.3, 0),
            WordEntry("world", 0.4, 0.7, 0),
            WordEntry("foo", 5.0, 5.3, 1),
        ]
        self.assertEqual(validate_alignment(words, 10.0, 2), [])

    def test_empty_word_list_is_valid(self):
        self.assertEqual(validate_alignment([], 10.0, 2), [])

    def test_empty_word_string(self):
        words = [WordEntry("", 0.1, 0.3, 0)]
        violations = validate_alignment(words, 10.0, 1)
        self.assertEqual(len(violations), 1)
        self.assertIn("empty word", violations[0])

    def test_whitespace_word_string(self):
        words = [WordEntry("  ", 0.1, 0.3, 0)]
        violations = validate_alignment(words, 10.0, 1)
        self.assertEqual(len(violations), 1)
        self.assertIn("empty word", violations[0])

    def test_negative_start(self):
        words = [WordEntry("hi", -0.5, 0.3, 0)]
        violations = validate_alignment(words, 10.0, 1)
        self.assertTrue(any("start -0.5 < 0" in v for v in violations))

    def test_negative_end(self):
        words = [WordEntry("hi", 0.1, -0.3, 0)]
        violations = validate_alignment(words, 10.0, 1)
        self.assertTrue(any("end -0.3 < 0" in v for v in violations))

    def test_start_exceeds_duration(self):
        words = [WordEntry("hi", 15.0, 15.5, 0)]
        violations = validate_alignment(words, 10.0, 1)
        self.assertTrue(any("start 15.0 > audio_duration 10.0" in v for v in violations))

    def test_end_exceeds_duration(self):
        words = [WordEntry("hi", 9.0, 11.0, 0)]
        violations = validate_alignment(words, 10.0, 1)
        self.assertTrue(any("end 11.0 > audio_duration 10.0" in v for v in violations))

    def test_chunk_idx_negative(self):
        words = [WordEntry("hi", 0.1, 0.3, -1)]
        violations = validate_alignment(words, 10.0, 2)
        self.assertTrue(any("chunk_idx -1 out of range" in v for v in violations))

    def test_chunk_idx_too_large(self):
        words = [WordEntry("hi", 0.1, 0.3, 5)]
        violations = validate_alignment(words, 10.0, 2)
        self.assertTrue(any("chunk_idx 5 out of range" in v for v in violations))

    def test_non_monotonic_start(self):
        words = [
            WordEntry("first", 1.0, 1.5, 0),
            WordEntry("second", 0.5, 0.8, 0),
        ]
        violations = validate_alignment(words, 10.0, 1)
        self.assertTrue(any("start 0.5 < previous start 1.0" in v for v in violations))

    def test_multiple_violations(self):
        words = [
            WordEntry("", -1.0, 0.3, 5),
        ]
        violations = validate_alignment(words, 10.0, 2)
        self.assertGreater(len(violations), 1)


class TestAlignChapter(unittest.TestCase):

    def _make_whisperx_mock(self, word_segments, audio_samples=160000):
        mock_wx = MagicMock()
        # load_audio returns a numpy-like array; len() must work (16kHz * 10s = 160000)
        mock_wx.load_audio.return_value = [0.0] * audio_samples
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {"word_segments": word_segments}
        return mock_wx

    def test_returns_word_entries(self):
        word_segs = [
            {"word": "Hello", "start": 0.1, "end": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
        ]
        mock_wx = self._make_whisperx_mock(word_segs)

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.m4a",
                ["Hello world"],
                [0.0],
            )

        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], WordEntry)
        self.assertEqual(result[0].word, "Hello")
        self.assertEqual(result[0].chunk_idx, 0)

    @patch.dict("sys.modules", {})
    def test_skipped_chunks_excluded_from_segments(self):
        word_segs = [{"word": "good", "start": 5.1, "end": 5.5}]
        mock_wx = MagicMock()
        mock_wx.load_audio.return_value = [0.0] * 160000
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {"word_segments": word_segs}

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.m4a",
                ["bad chunk", "good chunk"],
                [-1.0, 5.0],
            )

        # Segments passed to whisperx.align should only contain chunk 1
        call_args = mock_wx.align.call_args[0]
        segments_arg = call_args[0]
        self.assertEqual(len(segments_arg), 1)
        self.assertAlmostEqual(segments_arg[0]["start"], 5.0)

    def test_all_skipped_returns_empty(self):
        mock_wx = MagicMock()
        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.m4a",
                ["chunk one", "chunk two"],
                [-1.0, -1.0],
            )
        self.assertEqual(result, [])
        mock_wx.load_audio.assert_not_called()

    def test_final_multi_sentence_chunk_uses_audio_duration_boundary(self):
        mock_wx = self._make_whisperx_mock([], audio_samples=160000)

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            align_chapter(
                "/tmp/ch.m4a",
                ["First sentence. Second sentence. Third sentence."],
                [0.0],
            )

        segments_arg = mock_wx.align.call_args[0][0]
        self.assertEqual(
            [seg["text"] for seg in segments_arg],
            ["First sentence.", "Second sentence.", "Third sentence."],
        )
        for seg in segments_arg:
            self.assertGreaterEqual(seg["start"], 0.0)
            self.assertLessEqual(seg["end"], 10.0)
            self.assertGreater(seg["end"], seg["start"])

    def test_whisperx_runtime_failure_returns_empty(self):
        mock_wx = MagicMock()
        mock_wx.load_audio.side_effect = RuntimeError("model load failed")

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.m4a",
                ["Hello world"],
                [0.0],
            )

        self.assertEqual(result, [])

    def test_whisperx_not_installed_raises(self):
        # ImportError should propagate — missing dep is a setup problem
        with patch.dict("sys.modules", {"whisperx": None}):
            with self.assertRaises(ImportError):
                align_chapter("/tmp/ch.m4a", ["Hello world"], [0.0])

    def test_word_assigned_to_correct_chunk(self):
        word_segs = [
            {"word": "first",  "start": 0.1, "end": 0.5},
            {"word": "second", "start": 5.1, "end": 5.5},
        ]
        mock_wx = MagicMock()
        mock_wx.load_audio.return_value = [0.0] * 160000
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {"word_segments": word_segs}

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.m4a",
                ["chunk one", "chunk two"],
                [0.0, 5.0],
            )

        self.assertEqual(result[0].chunk_idx, 0)
        self.assertEqual(result[1].chunk_idx, 1)


class TestWriteWordAlignment(unittest.TestCase):

    def test_writes_json(self):
        chapters = [
            {
                "number": 1,
                "words": [
                    WordEntry(word="Hello", start=0.0, end=0.3, chunk_idx=0),
                    WordEntry(word="world", start=0.4, end=0.7, chunk_idx=0),
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "word_alignment.json")
            write_word_alignment(chapters, "kokoro-af-heart", path)

            with open(path) as f:
                data = json.load(f)

        self.assertEqual(data["version"], 1)
        self.assertEqual(data["rendition"], "kokoro-af-heart")
        self.assertEqual(len(data["chapters"]), 1)
        self.assertEqual(data["chapters"][0]["number"], 1)
        words = data["chapters"][0]["words"]
        self.assertEqual(words[0]["word"], "Hello")
        self.assertEqual(words[0]["chunk_idx"], 0)

    def test_idempotent_skips_existing(self):
        chapters = [{"number": 1, "words": []}]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "word_alignment.json")
            # Write initial file
            write_word_alignment(chapters, "rendition-a", path)
            mtime1 = os.path.getmtime(path)

            # Second call should skip
            write_word_alignment(chapters, "rendition-b", path)
            mtime2 = os.path.getmtime(path)

        self.assertEqual(mtime1, mtime2)


if __name__ == "__main__":
    unittest.main()
