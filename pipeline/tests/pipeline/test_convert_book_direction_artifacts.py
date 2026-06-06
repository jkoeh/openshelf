"""Tests for convert-book direction artifact helpers."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec  # noqa: E402

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "scripts",
    "convert-book.py",
)
spec = importlib.util.spec_from_file_location("convert_book_script", SCRIPT_PATH)
convert_book_script = importlib.util.module_from_spec(spec)
sys.modules["convert_book_script"] = convert_book_script
spec.loader.exec_module(convert_book_script)


class TestConvertBookDirectionArtifacts(unittest.TestCase):
    def test_chapter_direction_payload_and_speed_summary(self):
        ch_data = {
            "number": 3,
            "title": "Chapter",
            "chunks": [
                SimpleNamespace(text="Narrator. Quote."),
            ],
        }
        directed_chunks = [[
            DirectedSegment(
                text="Narrator.",
                voice=VoiceSpec(id="narrator"),
                speaker="narrator",
                speed=0.95,
                original_text="Narrator.",
            ),
            DirectedSegment(
                text="Quote.",
                voice=VoiceSpec(id="rabbit"),
                speaker="White Rabbit",
                speed=1.05,
                original_text="Quote.",
            ),
        ]]

        chapter = convert_book_script._build_direction_chapter(ch_data, directed_chunks)
        payload = convert_book_script._build_voice_direction_payload(
            "kokoro-af-heart",
            "abc123",
            "kokoro",
            "solo",
            [chapter],
        )
        summary = convert_book_script._direction_speed_summary(chapter)

        self.assertEqual(payload["cast_mode"], "solo")
        self.assertEqual(payload["chapters"][0]["number"], 3)
        self.assertEqual(summary["segments"], 2)
        self.assertEqual(summary["speed_counts"], {"0.95": 1, "1.05": 1})
        self.assertEqual(summary["above_1x"], 1)
        self.assertEqual(summary["narrator_above_1x"], 0)
        self.assertEqual(summary["max_speed"], 1.05)

    def test_chapter_direction_path_is_local_snapshot_name(self):
        path = convert_book_script._chapter_direction_path("build-dir", 7)

        self.assertEqual(
            path,
            os.path.join("build-dir", "chapter-07.voice_direction.json"),
        )

    def test_parse_chapter_filter(self):
        self.assertEqual(convert_book_script._parse_chapter_filter("2"), {2})
        self.assertEqual(convert_book_script._parse_chapter_filter("2,4-5"), {2, 4, 5})
        self.assertIsNone(convert_book_script._parse_chapter_filter(None))

    def test_parse_chapter_filter_rejects_invalid_range(self):
        with self.assertRaises(Exception):
            convert_book_script._parse_chapter_filter("5-2")


if __name__ == "__main__":
    unittest.main()
