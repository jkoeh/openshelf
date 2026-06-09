"""Tests for convert-book direction artifact helpers."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec  # noqa: E402
from openshelf.pipeline.text_chunker import Chunk, write_chapter_chunks_artifact  # noqa: E402
from openshelf.pipeline.tts import WordTimestamp  # noqa: E402

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
    def test_build_chapter_data_reads_chunk_artifact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = os.path.join(tmp, "chapter-01.chunks.json")
            write_chapter_chunks_artifact(
                artifact_path,
                1,
                "Chapter One",
                [Chunk("Reader text.", 0, 0, "ch1-el0000", "ch1-el0000")],
            )

            payload = convert_book_script._build_chapter_data(
                [{"number": 1, "chunks_artifact_path": artifact_path}],
                {1: [[WordTimestamp("Reader", 0.0, 0.4)]]},
                "kokoro-af-heart",
                "abc123",
            )

        self.assertEqual(payload["chapters"][0]["title"], "Chapter One")
        self.assertEqual(payload["chapters"][0]["word_count"], 2)
        self.assertEqual(payload["chapters"][0]["chunks"][0]["text"], "Reader text.")
        self.assertEqual(
            payload["chapters"][0]["chunks"][0]["words"][0],
            {"word": "Reader", "start": 0.0, "end": 0.4},
        )

    def test_build_chapter_data_reads_sync_artifact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks_path = os.path.join(tmp, "chapter-01.chunks.json")
            sync_path = os.path.join(tmp, "chapter-01.sync.json")
            write_chapter_chunks_artifact(
                chunks_path,
                1,
                "Chapter One",
                [Chunk("Reader text.", 0, 0, "ch1-el0000", "ch1-el0000")],
            )
            convert_book_script._write_chapter_sync_artifact(
                sync_path,
                1,
                "chapter-01.m4a",
                [0.0],
                [[WordTimestamp("Reader", 0.0, 0.4)]],
            )

            payload = convert_book_script._build_chapter_data(
                [{
                    "number": 1,
                    "chunks_artifact_path": chunks_path,
                    "sync_artifact_path": sync_path,
                }],
                {},
                "kokoro-af-heart",
                "abc123",
            )

        self.assertEqual(
            payload["chapters"][0]["chunks"][0]["words"],
            [{"word": "Reader", "start": 0.0, "end": 0.4}],
        )

    def test_chapter_sync_artifact_includes_coverage_metrics(self):
        artifact = convert_book_script._build_chapter_sync_artifact(
            1,
            "chapter-01.m4a",
            [0.0],
            [[WordTimestamp("Reader", 0.0, 0.4)]],
            chunk_texts=["Reader text."],
        )

        self.assertEqual(
            artifact["coverage"],
            {
                "reader_word_count": 2,
                "aligned_word_count": 1,
                "coverage_ratio": 0.5,
                "first_missing_word_offset": 1,
                "chunks": [
                    {
                        "index": 0,
                        "reader_word_count": 2,
                        "aligned_word_count": 1,
                        "coverage_ratio": 0.5,
                        "first_missing_word_offset": 1,
                    },
                ],
            },
        )

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

    def test_loads_directed_segments_from_chapter_direction_artifact(self):
        ch_data = {
            "number": 3,
            "title": "Chapter",
            "chunks": [SimpleNamespace(text="Narrator. Quote.")],
        }
        chapter = convert_book_script._build_direction_chapter(
            ch_data,
            [[
                DirectedSegment(
                    text="Directed narrator.",
                    voice=VoiceSpec(id="narrator", preset_name="af_heart"),
                    speaker="narrator",
                    emotion="gentle",
                    speed=0.95,
                    pause_after_ms=120,
                    original_text="Narrator.",
                    delivery_type="narration",
                    voice_policy="narrator",
                    join_policy="tight",
                    engine_controls={"style": "soft"},
                ),
            ]],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chapter-03.voice_direction.json")
            convert_book_script._write_json(
                path,
                convert_book_script._build_voice_direction_payload(
                    "kokoro-af-heart",
                    "abc123",
                    "kokoro",
                    "solo",
                    [chapter],
                ),
            )

            directed_chunks = convert_book_script._directed_chunks_from_chapter_direction_artifact(path)

        self.assertEqual(len(directed_chunks), 1)
        self.assertEqual(directed_chunks[0][0].text, "Directed narrator.")
        self.assertEqual(directed_chunks[0][0].voice.preset_name, "af_heart")
        self.assertEqual(directed_chunks[0][0].emotion, "gentle")
        self.assertEqual(directed_chunks[0][0].speed, 0.95)
        self.assertEqual(directed_chunks[0][0].pause_after_ms, 120)
        self.assertEqual(directed_chunks[0][0].original_text, "Narrator.")
        self.assertEqual(directed_chunks[0][0].join_policy, "tight")
        self.assertEqual(directed_chunks[0][0].engine_controls, {"style": "soft"})

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
