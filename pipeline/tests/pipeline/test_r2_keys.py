"""Tests for r2_keys — pure-string R2 key constructors.

Every R2 key construction in the pipeline goes through this module so the
worker-side mirror (worker/src/utils/r2-keys.ts) can be tested for the
same outputs against equivalent test names. See pipeline/docs/step6-r2.md
for the layout these keys describe.
"""

import os
import sys
import unittest

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline import r2_keys


AUTHOR = "franz-kafka"
TITLE = "the-metamorphosis"
RENDITION = "kokoro-af-heart"
BUILD = "2a4f9c1"


class TestBookLevelKeys(unittest.TestCase):
    """Keys that live directly under books/{author}/{title}/. These are not
    rendition- or build-scoped."""

    def test_book_prefix(self):
        self.assertEqual(
            r2_keys.book_prefix(AUTHOR, TITLE),
            "books/franz-kafka/the-metamorphosis",
        )

    def test_book_manifest_key(self):
        self.assertEqual(
            r2_keys.book_manifest_key(AUTHOR, TITLE),
            "books/franz-kafka/the-metamorphosis/manifest.json",
        )

    def test_book_epub_key(self):
        self.assertEqual(
            r2_keys.book_epub_key(AUTHOR, TITLE),
            "books/franz-kafka/the-metamorphosis/book.epub",
        )

    def test_cover_key_defaults_to_jpg(self):
        self.assertEqual(
            r2_keys.cover_key(AUTHOR, TITLE),
            "books/franz-kafka/the-metamorphosis/cover.jpg",
        )

    def test_cover_key_jpeg_content_type(self):
        self.assertEqual(
            r2_keys.cover_key(AUTHOR, TITLE, content_type="image/jpeg"),
            "books/franz-kafka/the-metamorphosis/cover.jpg",
        )

    def test_cover_key_png_content_type(self):
        self.assertEqual(
            r2_keys.cover_key(AUTHOR, TITLE, content_type="image/png"),
            "books/franz-kafka/the-metamorphosis/cover.png",
        )


class TestBuildScopedKeys(unittest.TestCase):
    """Keys that live under audio/{rendition}/builds/{build}/. Every one of
    these is content-addressed: same URL ⇒ same bytes forever."""

    def test_build_prefix(self):
        self.assertEqual(
            r2_keys.build_prefix(AUTHOR, TITLE, RENDITION, BUILD),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1",
        )

    def test_audio_key_zero_pads_chapter_number(self):
        self.assertEqual(
            r2_keys.audio_key(AUTHOR, TITLE, RENDITION, BUILD, 1),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1/chapter-01.m4a",
        )

    def test_audio_key_double_digit_chapter(self):
        self.assertEqual(
            r2_keys.audio_key(AUTHOR, TITLE, RENDITION, BUILD, 42),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1/chapter-42.m4a",
        )

    def test_chapter_data_key(self):
        self.assertEqual(
            r2_keys.chapter_data_key(AUTHOR, TITLE, RENDITION, BUILD),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1/chapter_data.json",
        )

    def test_character_registry_key(self):
        self.assertEqual(
            r2_keys.character_registry_key(AUTHOR, TITLE, RENDITION, BUILD),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1/character_registry.json",
        )

    def test_voice_direction_key(self):
        self.assertEqual(
            r2_keys.voice_direction_key(AUTHOR, TITLE, RENDITION, BUILD),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1/voice_direction.json",
        )

    def test_rendition_manifest_key(self):
        self.assertEqual(
            r2_keys.rendition_manifest_key(AUTHOR, TITLE, RENDITION, BUILD),
            "books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1/rendition-manifest.json",
        )


class TestRoutingThroughBookPrefix(unittest.TestCase):
    """All other keys start with the book_prefix — guard against drift."""

    def _starts_with_book_prefix(self, key: str) -> None:
        prefix = r2_keys.book_prefix(AUTHOR, TITLE)
        self.assertTrue(
            key.startswith(prefix + "/"),
            f"{key!r} does not start with {prefix!r}/",
        )

    def test_book_manifest_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.book_manifest_key(AUTHOR, TITLE))

    def test_book_epub_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.book_epub_key(AUTHOR, TITLE))

    def test_cover_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.cover_key(AUTHOR, TITLE))

    def test_audio_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.audio_key(AUTHOR, TITLE, RENDITION, BUILD, 1))

    def test_chapter_data_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.chapter_data_key(AUTHOR, TITLE, RENDITION, BUILD))

    def test_character_registry_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.character_registry_key(AUTHOR, TITLE, RENDITION, BUILD))

    def test_voice_direction_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.voice_direction_key(AUTHOR, TITLE, RENDITION, BUILD))

    def test_rendition_manifest_under_prefix(self):
        self._starts_with_book_prefix(r2_keys.rendition_manifest_key(AUTHOR, TITLE, RENDITION, BUILD))


if __name__ == "__main__":
    unittest.main()
