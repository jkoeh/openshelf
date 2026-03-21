"""Tests for manifest.py — Step 5 of the pipeline."""

import os
import sys
import unittest
from unittest.mock import patch, mock_open, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.manifest import ChapterMeta, generate_manifest


def _chapters() -> list[ChapterMeta]:
    return [
        ChapterMeta(number=1, title="Chapter One", filename="chapter-01.mp3",
                    duration_seconds=60.0, word_count=200),
        ChapterMeta(number=2, title="Chapter Two", filename="chapter-02.mp3",
                    duration_seconds=90.0, word_count=300),
    ]


class TestChapterMeta(unittest.TestCase):

    def test_fields(self):
        ch = ChapterMeta(number=3, title="The Beginning", filename="chapter-03.mp3",
                         duration_seconds=120.5, word_count=450)
        self.assertEqual(ch.number, 3)
        self.assertEqual(ch.title, "The Beginning")
        self.assertEqual(ch.filename, "chapter-03.mp3")
        self.assertAlmostEqual(ch.duration_seconds, 120.5)
        self.assertEqual(ch.word_count, 450)


class TestGenerateManifestIO(unittest.TestCase):

    @patch("openshelf.pipeline.manifest.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    @patch("openshelf.pipeline.manifest.os.path.exists", return_value=False)
    def test_writes_json_file(self, mock_exists, mock_file, mock_makedirs):
        generate_manifest("Franz Kafka", "The Trial", "gutenberg", _chapters(),
                          "/tmp/audio/kafka/the-trial")
        mock_file.assert_called_once()

    @patch("openshelf.pipeline.manifest.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    @patch("openshelf.pipeline.manifest.os.path.exists", return_value=False)
    def test_returns_manifest_path(self, mock_exists, mock_file, mock_makedirs):
        result = generate_manifest("Franz Kafka", "The Trial", "gutenberg", _chapters(),
                                   "/tmp/audio/kafka/the-trial")
        self.assertTrue(result.endswith("manifest.json"))

    @patch("openshelf.pipeline.manifest.os.makedirs")
    @patch("openshelf.pipeline.manifest.os.path.exists", return_value=True)
    def test_skips_if_exists(self, mock_exists, mock_makedirs):
        with patch("builtins.open", mock_open()) as mock_file:
            result = generate_manifest("Franz Kafka", "The Trial", "gutenberg", _chapters(),
                                       "/tmp/audio/kafka/the-trial")
            mock_file.assert_not_called()
        self.assertTrue(result.endswith("manifest.json"))

    @patch("openshelf.pipeline.manifest.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    @patch("openshelf.pipeline.manifest.os.path.exists", return_value=False)
    def test_creates_output_directory(self, mock_exists, mock_file, mock_makedirs):
        generate_manifest("Franz Kafka", "The Trial", "gutenberg", _chapters(),
                          "/tmp/audio/kafka/the-trial")
        mock_makedirs.assert_called_once_with("/tmp/audio/kafka/the-trial", exist_ok=True)


class TestGenerateManifestContent(unittest.TestCase):

    def _run(self, author="Franz Kafka", title="The Trial", source="gutenberg",
             chapters=None, output_dir="/tmp/audio/kafka/the-trial"):
        """Run generate_manifest and capture what was passed to json.dump."""
        if chapters is None:
            chapters = _chapters()
        captured = {}
        with patch("openshelf.pipeline.manifest.os.path.exists", return_value=False), \
             patch("openshelf.pipeline.manifest.os.makedirs"), \
             patch("builtins.open", mock_open()), \
             patch("openshelf.pipeline.manifest.json.dump",
                   side_effect=lambda data, *a, **kw: captured.update(data)):
            generate_manifest(author, title, source, chapters, output_dir)
        return captured

    def test_manifest_has_title(self):
        manifest = self._run()
        self.assertEqual(manifest["title"], "The Trial")

    def test_manifest_has_author(self):
        manifest = self._run()
        self.assertEqual(manifest["author"], "Franz Kafka")

    def test_manifest_has_source(self):
        manifest = self._run()
        self.assertEqual(manifest["source"], "gutenberg")

    def test_manifest_has_chapters(self):
        manifest = self._run()
        self.assertEqual(len(manifest["chapters"]), 2)

    def test_chapter_fields(self):
        manifest = self._run()
        ch = manifest["chapters"][0]
        self.assertEqual(ch["number"], 1)
        self.assertEqual(ch["title"], "Chapter One")
        self.assertEqual(ch["filename"], "chapter-01.mp3")
        self.assertEqual(ch["duration_seconds"], 60.0)
        self.assertEqual(ch["word_count"], 200)

    def test_manifest_has_generated_at(self):
        from datetime import datetime
        manifest = self._run()
        self.assertIn("generated_at", manifest)
        datetime.fromisoformat(manifest["generated_at"])  # raises if invalid format

    def test_manifest_total_duration(self):
        manifest = self._run()
        self.assertAlmostEqual(manifest["total_duration_seconds"], 150.0)

    def test_total_duration_empty_chapters(self):
        manifest = self._run(chapters=[])
        self.assertAlmostEqual(manifest["total_duration_seconds"], 0.0)

    def test_single_chapter(self):
        ch = [ChapterMeta(number=1, title="Only Chapter", filename="chapter-01.mp3",
                          duration_seconds=45.0, word_count=150)]
        manifest = self._run(chapters=ch)
        self.assertEqual(len(manifest["chapters"]), 1)
        self.assertAlmostEqual(manifest["total_duration_seconds"], 45.0)

    def test_chapters_preserve_order(self):
        manifest = self._run()
        numbers = [ch["number"] for ch in manifest["chapters"]]
        self.assertEqual(numbers, [1, 2])

    def test_manifest_has_rendition(self):
        manifest = self._run()
        self.assertIn("rendition", manifest)

    def test_manifest_rendition_value(self):
        captured = {}
        with patch("openshelf.pipeline.manifest.os.path.exists", return_value=False), \
             patch("openshelf.pipeline.manifest.os.makedirs"), \
             patch("builtins.open", mock_open()), \
             patch("openshelf.pipeline.manifest.json.dump",
                   side_effect=lambda data, *a, **kw: captured.update(data)):
            generate_manifest("Kafka", "The Trial", "gutenberg", _chapters(),
                              "/tmp/out", rendition="kokoro-af-heart", chunks_version=1)
        self.assertEqual(captured["rendition"], "kokoro-af-heart")
        self.assertEqual(captured["chunks_version"], 1)

    def test_manifest_has_chunks_version(self):
        manifest = self._run()
        self.assertIn("chunks_version", manifest)


if __name__ == "__main__":
    unittest.main()
