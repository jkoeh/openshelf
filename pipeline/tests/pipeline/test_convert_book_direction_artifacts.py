"""Tests for DAG direction artifact helpers."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.dag import cli as dag_module  # noqa: E402
from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec  # noqa: E402
from openshelf.pipeline.voice_director import (  # noqa: E402
    build_direction_chapter,
    build_voice_direction_payload,
)

class TestConvertBookDirectionArtifacts(unittest.TestCase):
    def test_chapter_direction_payload_and_speed_summary(self):
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

        chapter = build_direction_chapter(3, "Chapter", ["Narrator. Quote."], directed_chunks)
        payload = build_voice_direction_payload(
            "kokoro-af-heart",
            "abc123",
            "kokoro",
            "solo",
            [chapter],
        )
        summary = dag_module._direction_speed_summary(chapter)

        self.assertEqual(payload["cast_mode"], "solo")
        self.assertEqual(payload["chapters"][0]["number"], 3)
        self.assertEqual(summary["segments"], 2)
        self.assertEqual(summary["speed_counts"], {"0.95": 1, "1.05": 1})
        self.assertEqual(summary["above_1x"], 1)
        self.assertEqual(summary["narrator_above_1x"], 0)
        self.assertEqual(summary["max_speed"], 1.05)

    def test_chapter_direction_path_is_local_snapshot_name(self):
        path = dag_module._chapter_direction_path("build-dir", 7)

        self.assertEqual(
            path,
            os.path.join("build-dir", "chapter-07.voice_direction.json"),
        )

    def test_parse_chapter_filter(self):
        self.assertEqual(dag_module._parse_chapter_filter("2"), {2})
        self.assertEqual(dag_module._parse_chapter_filter("2,4-5"), {2, 4, 5})
        self.assertIsNone(dag_module._parse_chapter_filter(None))

    def test_parse_chapter_filter_rejects_invalid_range(self):
        with self.assertRaises(Exception):
            dag_module._parse_chapter_filter("5-2")


if __name__ == "__main__":
    unittest.main()
