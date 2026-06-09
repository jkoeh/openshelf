"""Tests for file-to-file pipeline DAG commands."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import contextlib  # noqa: E402
import io  # noqa: E402

from openshelf.pipeline.dag_cli import collect_coverage, main  # noqa: E402
from openshelf.pipeline.text_chunker import Chunk, write_chapter_chunks_artifact  # noqa: E402


def _write_sync(path: str, number: int, coverage: dict, chunks=None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "number": number,
                "audio_filename": f"chapter-{number:02d}.m4a",
                "chunk_audio_starts": [0.0],
                "coverage": coverage,
                "chunks": chunks or [],
            },
            f,
        )


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


class TestDagCliCoverage(unittest.TestCase):
    def test_collect_coverage_aggregates_book_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "chapter-01.sync.json"),
                1,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 10,
                    "coverage_ratio": 1.0,
                    "first_missing_word_offset": None,
                },
            )
            _write_sync(
                os.path.join(tmp, "chapter-02.sync.json"),
                2,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 6,
                    "coverage_ratio": 0.6,
                    "first_missing_word_offset": 6,
                },
            )

            report = collect_coverage(tmp)

        self.assertEqual(report["reader_word_count"], 20)
        self.assertEqual(report["aligned_word_count"], 16)
        self.assertEqual(report["coverage_ratio"], 0.8)
        # First gap is in chapter 2 at chapter-local offset 6, after 10 chapter-1 words.
        self.assertEqual(report["first_missing_word_offset"], 16)
        self.assertEqual([c["number"] for c in report["chapters"]], [1, 2])

    def test_coverage_command_exits_zero_on_low_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "chapter-01.sync.json"),
                1,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 0,
                    "coverage_ratio": 0.0,
                    "first_missing_word_offset": 0,
                },
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = main(["coverage", "--build-dir", tmp])

        # Diagnostic only: low coverage never fails the command.
        self.assertEqual(exit_code, 0)
        self.assertIn("0/10", buffer.getvalue())

    def test_coverage_command_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "chapter-01.sync.json"),
                1,
                {
                    "reader_word_count": 4,
                    "aligned_word_count": 4,
                    "coverage_ratio": 1.0,
                    "first_missing_word_offset": None,
                },
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = main(["coverage", "--build-dir", tmp, "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["coverage_ratio"], 1.0)
        self.assertEqual(payload["chapters"][0]["number"], 1)

    def test_coverage_command_errors_when_no_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                collect_coverage(tmp)


if __name__ == "__main__":
    unittest.main()
