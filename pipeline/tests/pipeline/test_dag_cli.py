"""Tests for file-to-file pipeline DAG commands."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.dag_cli import main  # noqa: E402
from openshelf.pipeline.text_chunker import Chunk, write_chapter_chunks_artifact  # noqa: E402


class TestDagCliAssemble(unittest.TestCase):
    def test_assemble_writes_chapter_data_from_chunk_and_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks_path = os.path.join(tmp, "chapter-01.chunks.json")
            sync_path = os.path.join(tmp, "chapter-01.sync.json")
            write_chapter_chunks_artifact(
                chunks_path,
                1,
                "Chapter One",
                [Chunk("Reader text.", 0, 0, "ch1-el0000", "ch1-el0000")],
            )
            with open(sync_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "number": 1,
                        "audio_filename": "chapter-01.m4a",
                        "chunk_audio_starts": [0.0],
                        "coverage": {
                            "reader_word_count": 2,
                            "aligned_word_count": 1,
                            "coverage_ratio": 0.5,
                            "first_missing_word_offset": 1,
                            "chunks": [],
                        },
                        "chunks": [
                            {
                                "index": 0,
                                "words": [{"word": "Reader", "start": 0.0, "end": 0.4}],
                            },
                        ],
                    },
                    f,
                )

            exit_code = main([
                "assemble",
                "--build-dir",
                tmp,
                "--rendition",
                "kokoro-af-heart",
                "--build-id",
                "abc123",
            ])

            with open(os.path.join(tmp, "chapter_data.json"), "r", encoding="utf-8") as f:
                payload = json.load(f)

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rendition"], "kokoro-af-heart")
        self.assertEqual(payload["build"], "abc123")
        self.assertEqual(payload["chapters"][0]["title"], "Chapter One")
        self.assertEqual(payload["chapters"][0]["chunks"][0]["text"], "Reader text.")
        self.assertEqual(
            payload["chapters"][0]["chunks"][0]["words"],
            [{"word": "Reader", "start": 0.0, "end": 0.4}],
        )


if __name__ == "__main__":
    unittest.main()
