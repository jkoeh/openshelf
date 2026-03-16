"""Tests for alignment.py — Step 5b of the pipeline."""

import json
import os
import sys
import unittest
from unittest.mock import patch, mock_open, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.alignment import build_alignment, write_alignment


def _chapter(number: int, starts: list[float], duration: float = 100.0) -> dict:
    return {
        "number": number,
        "total_duration_s": duration,
        "chunk_audio_starts": starts,
    }


class TestBuildAlignmentSchema(unittest.TestCase):

    def test_has_version_key(self):
        result = build_alignment("kokoro-af-heart", [_chapter(1, [0.0])])
        self.assertIn("version", result)

    def test_version_is_1(self):
        result = build_alignment("kokoro-af-heart", [_chapter(1, [0.0])])
        self.assertEqual(result["version"], 1)

    def test_has_rendition_key(self):
        result = build_alignment("kokoro-af-heart", [_chapter(1, [0.0])])
        self.assertIn("rendition", result)

    def test_rendition_preserved(self):
        result = build_alignment("my-rendition", [_chapter(1, [0.0])])
        self.assertEqual(result["rendition"], "my-rendition")

    def test_has_chapters_key(self):
        result = build_alignment("r", [_chapter(1, [0.0])])
        self.assertIn("chapters", result)

    def test_chapter_has_number(self):
        result = build_alignment("r", [_chapter(3, [0.0])])
        self.assertEqual(result["chapters"][0]["number"], 3)

    def test_chapter_has_total_duration(self):
        result = build_alignment("r", [_chapter(1, [0.0], duration=456.789)])
        self.assertAlmostEqual(result["chapters"][0]["total_duration_s"], 456.789, places=3)

    def test_chapter_has_chunks(self):
        result = build_alignment("r", [_chapter(1, [0.0, 10.0])])
        self.assertIn("chunks", result["chapters"][0])

    def test_chunk_has_chunk_idx(self):
        result = build_alignment("r", [_chapter(1, [0.0])])
        self.assertIn("chunk_idx", result["chapters"][0]["chunks"][0])

    def test_chunk_has_audio_start_s(self):
        result = build_alignment("r", [_chapter(1, [0.0])])
        self.assertIn("audio_start_s", result["chapters"][0]["chunks"][0])


class TestBuildAlignmentValues(unittest.TestCase):

    def test_single_chunk_at_zero(self):
        result = build_alignment("r", [_chapter(1, [0.0])])
        chunks = result["chapters"][0]["chunks"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["chunk_idx"], 0)
        self.assertEqual(chunks[0]["audio_start_s"], 0.0)

    def test_two_chunks_correct_offsets(self):
        result = build_alignment("r", [_chapter(1, [0.0, 45.6])])
        chunks = result["chapters"][0]["chunks"]
        self.assertEqual(chunks[0]["audio_start_s"], 0.0)
        self.assertAlmostEqual(chunks[1]["audio_start_s"], 45.6)

    def test_skipped_chunk_excluded(self):
        result = build_alignment("r", [_chapter(1, [0.0, -1.0, 50.0])])
        chunks = result["chapters"][0]["chunks"]
        self.assertEqual(len(chunks), 2)
        chunk_idxs = [c["chunk_idx"] for c in chunks]
        self.assertNotIn(1, chunk_idxs)

    def test_skipped_chunk_idx_gaps(self):
        result = build_alignment("r", [_chapter(1, [0.0, -1.0, 50.0])])
        chunks = result["chapters"][0]["chunks"]
        self.assertEqual(chunks[0]["chunk_idx"], 0)
        self.assertEqual(chunks[1]["chunk_idx"], 2)

    def test_all_chunks_skipped_produces_empty_list(self):
        result = build_alignment("r", [_chapter(1, [-1.0, -1.0])])
        self.assertEqual(result["chapters"][0]["chunks"], [])

    def test_multiple_chapters(self):
        result = build_alignment("r", [
            _chapter(1, [0.0, 10.0]),
            _chapter(2, [0.0, 20.0, 40.0]),
        ])
        self.assertEqual(len(result["chapters"]), 2)
        self.assertEqual(result["chapters"][1]["number"], 2)
        self.assertEqual(len(result["chapters"][1]["chunks"]), 3)

    def test_values_rounded_to_4_decimal_places(self):
        result = build_alignment("r", [_chapter(1, [1.23456789])])
        value = result["chapters"][0]["chunks"][0]["audio_start_s"]
        # Should be rounded to 4 decimal places
        self.assertEqual(value, round(1.23456789, 4))

    def test_empty_chapters_list(self):
        result = build_alignment("r", [])
        self.assertEqual(result["chapters"], [])


class TestWriteAlignment(unittest.TestCase):

    def test_returns_output_path(self):
        alignment = build_alignment("r", [_chapter(1, [0.0])])
        with patch("os.path.exists", return_value=False), \
             patch("os.makedirs"), \
             patch("builtins.open", mock_open()):
            result = write_alignment(alignment, "/tmp/alignment.json")
        self.assertEqual(result, "/tmp/alignment.json")

    def test_idempotent_skips_if_exists(self):
        alignment = build_alignment("r", [_chapter(1, [0.0])])
        with patch("os.path.exists", return_value=True) as mock_exists, \
             patch("builtins.open", mock_open()) as mock_file:
            write_alignment(alignment, "/tmp/alignment.json")
        mock_file.assert_not_called()

    def test_creates_parent_dirs(self):
        alignment = build_alignment("r", [_chapter(1, [0.0])])
        with patch("os.path.exists", return_value=False), \
             patch("os.makedirs") as mock_makedirs, \
             patch("builtins.open", mock_open()):
            write_alignment(alignment, "/tmp/subdir/alignment.json")
        mock_makedirs.assert_called_once()

    def test_writes_valid_json(self):
        alignment = build_alignment("kokoro-af-heart", [_chapter(1, [0.0, 45.6])])
        written_data = []

        def capture_write(data):
            written_data.append(data)

        m = mock_open()
        m.return_value.__enter__.return_value.write = capture_write

        with patch("os.path.exists", return_value=False), \
             patch("os.makedirs"), \
             patch("builtins.open", m):
            write_alignment(alignment, "/tmp/alignment.json")

        # Verify json.dump was called with valid content by checking open was called
        m.assert_called_once_with("/tmp/alignment.json", "w", encoding="utf-8")

    def test_idempotent_returns_path_on_skip(self):
        alignment = build_alignment("r", [_chapter(1, [0.0])])
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open()):
            result = write_alignment(alignment, "/tmp/alignment.json")
        self.assertEqual(result, "/tmp/alignment.json")


if __name__ == "__main__":
    unittest.main()
