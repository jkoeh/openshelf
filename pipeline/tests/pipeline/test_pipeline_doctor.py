from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.ops.doctor import diagnose_build  # noqa: E402


def _write_json(path: str, payload: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _write_complete_chapter(build_dir: str, number: int, coverage_ratio: float = 1.0):
    _write_json(
        os.path.join(build_dir, f"chapter-{number:02d}.chunks.json"),
        {"number": number, "chunks": [{"text": "hello world"}]},
    )
    _write_json(
        os.path.join(build_dir, f"chapter-{number:02d}.voice_direction.json"),
        {"chapters": [{"number": number}]},
    )
    _write_json(
        os.path.join(build_dir, f"chapter-{number:02d}.sync.json"),
        {
            "number": number,
            "chunk_audio_starts": [0.0],
            "coverage": {
                "coverage_ratio": coverage_ratio,
                "reader_word_count": 2,
                "aligned_word_count": int(2 * coverage_ratio),
            },
        },
    )
    with open(os.path.join(build_dir, f"chapter-{number:02d}.m4a"), "wb") as f:
        f.write(b"audio")


def _write_book_artifacts(build_dir: str, chapters: list[int]):
    for name in ("run.json", "character_registry.json", "voice_direction.json"):
        _write_json(os.path.join(build_dir, name), {})
    _write_json(
        os.path.join(build_dir, "chapter_data.json"),
        {"chapters": [{"number": number} for number in chapters]},
    )
    _write_json(
        os.path.join(build_dir, "rendition-manifest.json"),
        {"chapters": [{"number": number} for number in chapters]},
    )


class TestPipelineDoctor(unittest.TestCase):
    def test_complete_build_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_complete_chapter(tmp, 1)
            _write_complete_chapter(tmp, 2)
            _write_book_artifacts(tmp, [1, 2])

            report = diagnose_build(tmp)

        self.assertEqual(report.status, "ok")
        self.assertEqual(report.counts["m4a"], 2)
        self.assertEqual(report.counts["sync"], 2)

    def test_missing_audio_and_sync_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_json(
                os.path.join(tmp, "chapter-01.chunks.json"),
                {"number": 1, "chunks": [{"text": "hello"}]},
            )
            _write_book_artifacts(tmp, [1])

            report = diagnose_build(tmp)

        self.assertEqual(report.status, "fail")
        messages = "\n".join(issue.message for issue in report.issues)
        self.assertIn("missing m4a", messages)
        self.assertIn("missing sync", messages)

    def test_low_coverage_is_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_complete_chapter(tmp, 1, coverage_ratio=0.5)
            _write_book_artifacts(tmp, [1])

            report = diagnose_build(tmp)

        self.assertEqual(report.status, "warn")
        self.assertIn("low sync coverage", report.issues[0].message)

    def test_known_ffmpeg_probe_traceback_is_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_complete_chapter(tmp, 1)
            _write_book_artifacts(tmp, [1])
            log = os.path.join(tmp, "run.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write(
                    "2026 DEBUG [torio._extension.utils] Loading FFmpeg6\n"
                    "2026 DEBUG [torio._extension.utils] Failed to load FFmpeg6 extension.\n"
                    "Traceback (most recent call last):\n"
                    "FileNotFoundError: libtorio_ffmpeg6.pyd\n"
                    "2026 INFO [openshelf.pipeline.dag.cli] Chapter 1 complete\n"
                )

            report = diagnose_build(tmp, log)

        self.assertEqual(report.status, "warn")
        self.assertTrue(any("known debug noise" in issue.message for issue in report.issues))

    def test_real_log_error_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_complete_chapter(tmp, 1)
            _write_book_artifacts(tmp, [1])
            log = os.path.join(tmp, "run.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write("2026 ERROR [openshelf.pipeline] Failed: bad thing\n")

            report = diagnose_build(tmp, log)

        self.assertEqual(report.status, "fail")


if __name__ == "__main__":
    unittest.main()
