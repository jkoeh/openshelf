"""Tests for manifest.py — the book manifest and per-build rendition manifest.

See pipeline/docs/step5a-manifest.md and step5c-rendition-manifest.md for
the shapes these functions produce. Key invariants:

- The book manifest is the only mutable per-book artifact; merge semantics
  prepend the new build, dedupe, and truncate to `retain` entries.
- The rendition manifest is byte-stable for fixed inputs (no `generated_at`,
  no wall-clock) so --force reruns produce byte-identical content for an
  unchanged build.
"""

import json
import os
import sys
import tempfile
import unittest

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.manifest import (
    ChapterMeta,
    RenditionEntry,
    generate_book_manifest,
    generate_rendition_entry,
    generate_rendition_manifest,
    merge_book_manifest,
)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _chapters() -> list[ChapterMeta]:
    return [
        ChapterMeta(number=1, title="I", filename="chapter-01.m4a",
                    duration_seconds=60.0, word_count=200),
        ChapterMeta(number=2, title="II", filename="chapter-02.m4a",
                    duration_seconds=90.0, word_count=300),
    ]


def _entry(build="2a4f9c1", voice="af_heart", engine="kokoro", display="Heart") -> RenditionEntry:
    return RenditionEntry(
        voice=voice,
        engine=engine,
        display=display,
        current_build=build,
        available_builds=[build],
    )


# ---------------------------------------------------------------------------
# ChapterMeta
# ---------------------------------------------------------------------------


class TestChapterMeta(unittest.TestCase):

    def test_fields(self):
        ch = ChapterMeta(number=3, title="III", filename="chapter-03.m4a",
                         duration_seconds=120.5, word_count=450)
        self.assertEqual(ch.number, 3)
        self.assertEqual(ch.title, "III")
        self.assertEqual(ch.filename, "chapter-03.m4a")
        self.assertAlmostEqual(ch.duration_seconds, 120.5)
        self.assertEqual(ch.word_count, 450)


# ---------------------------------------------------------------------------
# RenditionEntry + generate_rendition_entry
# ---------------------------------------------------------------------------


class TestGenerateRenditionEntry(unittest.TestCase):

    def test_constructs_from_build_id(self):
        entry = generate_rendition_entry(
            voice="af_heart", engine="kokoro", display="Heart", build_id="2a4f9c1",
        )
        self.assertEqual(entry.voice, "af_heart")
        self.assertEqual(entry.engine, "kokoro")
        self.assertEqual(entry.display, "Heart")
        self.assertEqual(entry.current_build, "2a4f9c1")
        self.assertEqual(entry.available_builds, ["2a4f9c1"])


# ---------------------------------------------------------------------------
# generate_book_manifest (from-scratch write)
# ---------------------------------------------------------------------------


class TestGenerateBookManifest(unittest.TestCase):

    def test_writes_manifest_json_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = generate_book_manifest(
                author="Franz Kafka", title="The Metamorphosis", source="gutenberg",
                renditions={"kokoro-af-heart": _entry()},
                output_dir=d,
            )
            self.assertTrue(path.endswith("manifest.json"))
            self.assertTrue(os.path.isfile(path))

    def test_manifest_top_level_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = generate_book_manifest(
                author="Franz Kafka", title="The Metamorphosis", source="gutenberg",
                renditions={"kokoro-af-heart": _entry()},
                output_dir=d,
            )
            data = _read_json(path)
            self.assertEqual(data["title"], "The Metamorphosis")
            self.assertEqual(data["author"], "Franz Kafka")
            self.assertEqual(data["source"], "gutenberg")

    def test_renditions_keyed_by_slug(self):
        with tempfile.TemporaryDirectory() as d:
            path = generate_book_manifest(
                author="A", title="T", source="s",
                renditions={"kokoro-af-heart": _entry(build="abc1234")},
                output_dir=d,
            )
            data = _read_json(path)
            self.assertIn("kokoro-af-heart", data["renditions"])
            entry = data["renditions"]["kokoro-af-heart"]
            self.assertEqual(entry["voice"], "af_heart")
            self.assertEqual(entry["engine"], "kokoro")
            self.assertEqual(entry["display"], "Heart")
            self.assertEqual(entry["current_build"], "abc1234")
            self.assertEqual(entry["available_builds"], ["abc1234"])

    def test_multiple_renditions(self):
        with tempfile.TemporaryDirectory() as d:
            path = generate_book_manifest(
                author="A", title="T", source="s",
                renditions={
                    "kokoro-af-heart": _entry(build="aaaaaaa", voice="af_heart", display="Heart"),
                    "kokoro-bf-emma":  _entry(build="bbbbbbb", voice="bf_emma",  display="Emma"),
                },
                output_dir=d,
            )
            data = _read_json(path)
            self.assertEqual(set(data["renditions"].keys()), {"kokoro-af-heart", "kokoro-bf-emma"})

    def test_no_chapters_field_on_book_manifest(self):
        """Chapter list lives in the per-build rendition manifest, not here."""
        with tempfile.TemporaryDirectory() as d:
            path = generate_book_manifest(
                author="A", title="T", source="s",
                renditions={"kokoro-af-heart": _entry()},
                output_dir=d,
            )
            data = _read_json(path)
            self.assertNotIn("chapters", data)
            self.assertNotIn("chapters", data["renditions"]["kokoro-af-heart"])


# ---------------------------------------------------------------------------
# merge_book_manifest (pure)
# ---------------------------------------------------------------------------


class TestMergeBookManifest(unittest.TestCase):

    def test_adds_to_empty_prior(self):
        prior = {"title": "T", "author": "A", "source": "s", "renditions": {}}
        new = _entry(build="new1234")
        merged = merge_book_manifest(prior, "kokoro-af-heart", new)
        self.assertIn("kokoro-af-heart", merged["renditions"])
        self.assertEqual(merged["renditions"]["kokoro-af-heart"]["current_build"], "new1234")
        self.assertEqual(merged["renditions"]["kokoro-af-heart"]["available_builds"], ["new1234"])

    def test_prior_with_no_renditions_field(self):
        prior = {"title": "T", "author": "A", "source": "s"}
        new = _entry(build="new1234")
        merged = merge_book_manifest(prior, "kokoro-af-heart", new)
        self.assertEqual(merged["renditions"]["kokoro-af-heart"]["current_build"], "new1234")

    def test_new_build_prepended_to_available_builds(self):
        prior = {"renditions": {"kokoro-af-heart": {
            "voice": "af_heart", "engine": "kokoro", "display": "Heart",
            "current_build": "oldbuld", "available_builds": ["oldbuld"],
        }}}
        new = _entry(build="newbuld")
        merged = merge_book_manifest(prior, "kokoro-af-heart", new)
        entry = merged["renditions"]["kokoro-af-heart"]
        self.assertEqual(entry["current_build"], "newbuld")
        self.assertEqual(entry["available_builds"], ["newbuld", "oldbuld"])

    def test_same_build_is_noop_no_duplicate(self):
        prior = {"renditions": {"kokoro-af-heart": {
            "voice": "af_heart", "engine": "kokoro", "display": "Heart",
            "current_build": "abc1234", "available_builds": ["abc1234", "older12"],
        }}}
        new = _entry(build="abc1234")
        merged = merge_book_manifest(prior, "kokoro-af-heart", new)
        entry = merged["renditions"]["kokoro-af-heart"]
        self.assertEqual(entry["available_builds"], ["abc1234", "older12"])

    def test_retain_truncates_older_builds(self):
        prior = {"renditions": {"kokoro-af-heart": {
            "voice": "af_heart", "engine": "kokoro", "display": "Heart",
            "current_build": "build02", "available_builds": ["build02", "build01", "build00"],
        }}}
        new = _entry(build="build03")
        merged = merge_book_manifest(prior, "kokoro-af-heart", new, retain=2)
        entry = merged["renditions"]["kokoro-af-heart"]
        self.assertEqual(entry["available_builds"], ["build03", "build02"])

    def test_other_renditions_preserved_unchanged(self):
        prior = {"renditions": {
            "kokoro-af-heart": {
                "voice": "af_heart", "engine": "kokoro", "display": "Heart",
                "current_build": "heart01", "available_builds": ["heart01"],
            },
            "kokoro-bf-emma": {
                "voice": "bf_emma", "engine": "kokoro", "display": "Emma",
                "current_build": "emma001", "available_builds": ["emma001"],
            },
        }}
        new = _entry(build="heart02")
        merged = merge_book_manifest(prior, "kokoro-af-heart", new)
        # Emma untouched
        self.assertEqual(merged["renditions"]["kokoro-bf-emma"]["current_build"], "emma001")
        self.assertEqual(merged["renditions"]["kokoro-bf-emma"]["available_builds"], ["emma001"])
        # Heart updated
        self.assertEqual(merged["renditions"]["kokoro-af-heart"]["current_build"], "heart02")

    def test_prior_not_mutated(self):
        prior = {"renditions": {"kokoro-af-heart": {
            "voice": "af_heart", "engine": "kokoro", "display": "Heart",
            "current_build": "oldbuld", "available_builds": ["oldbuld"],
        }}}
        new = _entry(build="newbuld")
        merge_book_manifest(prior, "kokoro-af-heart", new)
        # Caller's prior dict still intact
        self.assertEqual(prior["renditions"]["kokoro-af-heart"]["current_build"], "oldbuld")
        self.assertEqual(prior["renditions"]["kokoro-af-heart"]["available_builds"], ["oldbuld"])


# ---------------------------------------------------------------------------
# generate_rendition_manifest (per-build, byte-stable)
# ---------------------------------------------------------------------------


class TestGenerateRenditionManifest(unittest.TestCase):

    def _generate(self, output_dir, chapters=None):
        return generate_rendition_manifest(
            rendition="kokoro-af-heart",
            build_id="2a4f9c1",
            voice="af_heart",
            engine="kokoro",
            pipeline_version="1",
            chapters=chapters if chapters is not None else _chapters(),
            output_dir=output_dir,
        )

    def test_writes_rendition_manifest_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._generate(d)
            self.assertTrue(path.endswith("rendition-manifest.json"))
            self.assertTrue(os.path.isfile(path))

    def test_top_level_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._generate(d)
            data = _read_json(path)
            self.assertEqual(data["build"], "2a4f9c1")
            self.assertEqual(data["rendition"], "kokoro-af-heart")
            self.assertEqual(data["voice"], "af_heart")
            self.assertEqual(data["engine"], "kokoro")
            self.assertEqual(data["pipeline_version"], "1")

    def test_total_duration_is_sum(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._generate(d)
            data = _read_json(path)
            self.assertAlmostEqual(data["total_duration_seconds"], 150.0)

    def test_chapters_preserve_order_and_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._generate(d)
            data = _read_json(path)
            self.assertEqual([c["number"] for c in data["chapters"]], [1, 2])
            self.assertEqual(data["chapters"][0]["title"], "I")
            self.assertEqual(data["chapters"][0]["filename"], "chapter-01.m4a")
            self.assertEqual(data["chapters"][0]["duration_seconds"], 60.0)
            self.assertEqual(data["chapters"][0]["word_count"], 200)

    def test_no_generated_at_field(self):
        """Per byte-stability rule in step5c: no wall-clock timestamps."""
        with tempfile.TemporaryDirectory() as d:
            path = self._generate(d)
            data = _read_json(path)
            self.assertNotIn("generated_at", data)

    def test_byte_stable_for_same_inputs(self):
        """Two runs with identical inputs produce byte-identical files —
        this is what makes --force reruns safe under immutable cache headers."""
        with tempfile.TemporaryDirectory() as d:
            p1 = self._generate(os.path.join(d, "a"))
            p2 = self._generate(os.path.join(d, "b"))
            with open(p1, "rb") as f1, open(p2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())

    def test_empty_chapters(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._generate(d, chapters=[])
            data = _read_json(path)
            self.assertEqual(data["chapters"], [])
            self.assertAlmostEqual(data["total_duration_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
