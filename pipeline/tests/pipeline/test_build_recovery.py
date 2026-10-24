from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from openshelf.pipeline.build_recovery import adopt_local_build
from openshelf.pipeline.run_context import RunContext, RunContextError, write_run_context
from openshelf.pipeline.seams import ChunkSynthesisAudit
from openshelf.pipeline.synthesis_checkpoints import SynthesisCheckpoints, write_completion


class LocalBuildRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.source = root / "aaaaaaaaaaaaaaaa"
        self.target = root / "bbbbbbbbbbbbbbbb"
        self.source.mkdir()
        self.context = RunContext(
            build=self.source.name, pipeline_version="2", epub_path="book.epub", epub_sha256="sourcehash",
            author_slug="author", title_slug="title", book_author="作者", book_title="紅樓夢",
            engine="qwen3", voice="qwen3-serena", rendition="qwen3-serena", cast_mode="solo",
            performance_direction_mode="batched", language="zh", sections=[1],
            engine_settings={"model": "pinned"},
        )
        self.expected = replace(self.context, build=self.target.name)
        write_run_context(self.source / "run.json", self.context)
        (self.source / "section-01.chunks.json").write_text('{"chunks":[]}', encoding="utf-8")
        (self.source / "section-01.voice_direction.json").write_text(json.dumps(
            {"build": self.source.name, "chapters": []}), encoding="utf-8")

    def complete(self):
        files = [self.source / ("section-01" + suffix) for suffix in
                 (".m4a", ".sync.json", ".synthesis_units.json")]
        for file in files:
            file.write_bytes(b"validated fixture bytes")
        write_completion(self.source / "section-01.complete.json", "fingerprint",
                         [str(file) for file in files], {"skipped_chunks": 0}, 1.0)

    def test_adopts_only_completed_bytes_and_rewrites_direction_build(self):
        self.complete()
        (self.source / "rendition-manifest.json").write_text("never copy", encoding="utf-8")
        audit = adopt_local_build(self.source, self.target, self.expected)
        self.assertEqual((self.target / "section-01.m4a").read_bytes(), b"validated fixture bytes")
        direction = json.loads((self.target / "section-01.voice_direction.json").read_text(encoding="utf-8"))
        self.assertEqual(direction["build"], self.target.name)
        self.assertEqual(audit["source_build"], self.source.name)
        self.assertTrue(audit["sections"][0]["completed"])
        self.assertFalse((self.target / "rendition-manifest.json").exists())
        self.assertEqual(json.loads((self.target / "run.json").read_text(encoding="utf-8"))["build"], self.target.name)

    def test_corrupt_completion_is_not_adopted_but_valid_region_is(self):
        self.complete()
        (self.source / "section-01.m4a").write_bytes(b"different bytes")
        cache = SynthesisCheckpoints(self.source / "section-01.checkpoints", 24000)
        cache.save("chunk-0000", "fingerprint", np.array([0.1, -0.1], dtype=np.float32), [],
                   ChunkSynthesisAudit(0, 0, 2), "original context")
        audit = adopt_local_build(self.source, self.target, self.expected)
        self.assertFalse((self.target / "section-01.m4a").exists())
        self.assertFalse(audit["sections"][0]["completed"])
        region = SynthesisCheckpoints(self.target / "section-01.checkpoints", 24000).load("chunk-0000", "fingerprint")
        self.assertEqual(region.context_after, "original context")

    def test_mismatched_source_or_existing_target_is_rejected(self):
        with self.assertRaises(RunContextError):
            adopt_local_build(self.source, self.target, replace(self.expected, epub_sha256="wrong source"))
        self.assertFalse(self.target.exists())
        self.target.mkdir()
        (self.target / "existing").write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            adopt_local_build(self.source, self.target, self.expected)
        self.assertEqual((self.target / "existing").read_text(encoding="utf-8"), "preserve")

    def test_unproven_legacy_audio_is_not_adopted(self):
        (self.source / "section-01.m4a").write_bytes(b"no completion receipt")
        audit = adopt_local_build(self.source, self.target, self.expected)
        self.assertEqual(audit["sections"], [])
        self.assertFalse((self.target / "section-01.m4a").exists())
