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


class TestAlignChapter(unittest.TestCase):

    def _make_whisperx_mock(self, word_segments):
        mock_wx = MagicMock()
        mock_wx.load_audio.return_value = MagicMock()
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
                "/tmp/ch.opus",
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
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {"word_segments": word_segs}

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.opus",
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
                "/tmp/ch.opus",
                ["chunk one", "chunk two"],
                [-1.0, -1.0],
            )
        self.assertEqual(result, [])
        mock_wx.load_audio.assert_not_called()

    def test_word_assigned_to_correct_chunk(self):
        word_segs = [
            {"word": "first",  "start": 0.1, "end": 0.5},
            {"word": "second", "start": 5.1, "end": 5.5},
        ]
        mock_wx = MagicMock()
        mock_wx.load_audio.return_value = MagicMock()
        mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())
        mock_wx.align.return_value = {"word_segments": word_segs}

        with patch.dict("sys.modules", {"whisperx": mock_wx}):
            result = align_chapter(
                "/tmp/ch.opus",
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
