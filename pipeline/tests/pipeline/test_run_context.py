"""Tests for per-build run context validation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.run_context import (  # noqa: E402
    RunContextError,
    make_run_context,
    prepare_run_context,
    run_context_path,
    validate_build_id,
)


def _write_epub_fixture(tmp: str, content: bytes = b"book") -> str:
    path = os.path.join(tmp, "book.epub")
    with open(path, "wb") as f:
        f.write(content)
    return path


def _context(tmp: str, **overrides):
    epub_path = overrides.pop("epub_path", _write_epub_fixture(tmp))
    params = {
        "build": "2a4f9c1b3d8e7f60",
        "pipeline_version": "1",
        "epub_path": epub_path,
        "author_slug": "lewis-carroll",
        "title_slug": "alice",
        "book_author": "Lewis Carroll",
        "book_title": "Alice",
        "engine": "kokoro",
        "voice": "af_heart",
        "rendition": "kokoro-af-heart",
        "cast_mode": "solo",
        "performance_direction_mode": "batched",
        "language": "en",
        "chapters": [2, 1],
    }
    params.update(overrides)
    return make_run_context(**params)


class TestBuildIdValidation(unittest.TestCase):
    def test_accepts_16_hex_and_normalizes_uppercase(self):
        self.assertEqual(validate_build_id("2A4F9C1B3D8E7F60"), "2a4f9c1b3d8e7f60")

    def test_rejects_invalid_build_id(self):
        with self.assertRaisesRegex(RunContextError, "16 lowercase hex"):
            validate_build_id("abc123")


class TestRunContext(unittest.TestCase):
    def test_make_run_context_hashes_epub_and_sorts_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)

        data = context.to_dict()
        self.assertEqual(data["chapters"], [1, 2])
        self.assertEqual(data["performance_direction_mode"], "batched")
        self.assertEqual(data["epub_sha256"], "92719fe0cf8cd51592af31ee8a5736d79f7273777fa3f7b70bfe993a4cd32180")

    def test_prepare_writes_new_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)
            path = run_context_path(os.path.join(tmp, "build"))

            prepare_run_context(path, context)

            with open(path, "r", encoding="utf-8") as f:
                written = json.load(f)
        self.assertEqual(written["build"], "2a4f9c1b3d8e7f60")

    def test_existing_context_requires_resume_or_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)
            path = run_context_path(os.path.join(tmp, "build"))
            prepare_run_context(path, context)

            with self.assertRaisesRegex(RunContextError, "pass --resume or --force"):
                prepare_run_context(path, context)

    def test_resume_accepts_matching_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)
            path = run_context_path(os.path.join(tmp, "build"))
            prepare_run_context(path, context)

            result = prepare_run_context(path, context, resume=True)

        self.assertEqual(result, path)

    def test_resume_rejects_mismatched_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)
            path = run_context_path(os.path.join(tmp, "build"))
            prepare_run_context(path, context)
            mismatch = _context(tmp, engine="chatterbox")

            with self.assertRaisesRegex(RunContextError, "engine"):
                prepare_run_context(path, mismatch, resume=True)

    def test_resume_rejects_mismatched_performance_direction_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)
            path = run_context_path(os.path.join(tmp, "build"))
            prepare_run_context(path, context)
            mismatch = _context(tmp, performance_direction_mode="off")

            with self.assertRaisesRegex(RunContextError, "performance_direction_mode"):
                prepare_run_context(path, mismatch, resume=True)

    def test_force_overwrites_mismatched_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _context(tmp)
            path = run_context_path(os.path.join(tmp, "build"))
            prepare_run_context(path, context)
            replacement = _context(tmp, engine="chatterbox")

            prepare_run_context(path, replacement, force=True)

            with open(path, "r", encoding="utf-8") as f:
                written = json.load(f)
        self.assertEqual(written["engine"], "chatterbox")


if __name__ == "__main__":
    unittest.main()
