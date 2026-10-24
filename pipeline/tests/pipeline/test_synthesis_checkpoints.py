import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from openshelf.pipeline.seams import ChunkSynthesisAudit, SegmentSynthesisAudit, SynthesisUnitAudit
from openshelf.pipeline.synthesis_checkpoints import SynthesisCheckpoints, input_fingerprint
from openshelf.pipeline.tts_engine import WordTimestamp
from openshelf.pipeline.tts import ChunkInfo, synthesize_chapter, synthesize_chapter_to_files
from openshelf.pipeline.tts_engine import PostProcessingConfig, TTSCapabilities, VoiceSpec


class SynthesisCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = SynthesisCheckpoints(self.directory.name, 24000)
        self.audio = np.array([0.0, 0.1, -0.25, 0.5], dtype=np.float32)
        self.words = [WordTimestamp("紅", 0.01, 0.03)]
        self.audit = ChunkSynthesisAudit(0, 240, 244, [
            SegmentSynthesisAudit(0, "narrator", None, 240, 244,
                                  [SynthesisUnitAudit(0, "紅。", 240, 244)]),
        ])

    def save(self):
        self.cache.save("chunk-0000", "identity", self.audio, self.words, self.audit)

    def test_round_trip_preserves_float_audio_words_and_nested_audit(self):
        self.save()
        result = self.cache.load("chunk-0000", "identity")
        np.testing.assert_array_equal(result.audio, self.audio)
        self.assertEqual(result.words, self.words)
        self.assertEqual(result.audit, self.audit)
        self.assertIsNone(self.cache.load("chunk-0000", "changed text or engine"))

    def test_corrupt_audio_or_missing_commit_is_a_miss(self):
        self.save()
        path = Path(self.directory.name) / "chunk-0000.wav"
        path.write_bytes(path.read_bytes()[:-1])
        self.assertIsNone(self.cache.load("chunk-0000", "identity"))
        self.assertIsNone(self.cache.load("heading", "identity"))

    def test_invalid_timestamp_or_metadata_is_a_miss(self):
        self.save()
        path = Path(self.directory.name) / "chunk-0000.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["words"][0]["start"] = -1
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(self.cache.load("chunk-0000", "identity"))
        path.write_text("partial", encoding="utf-8")
        self.assertIsNone(self.cache.load("chunk-0000", "identity"))

    def test_rejects_nonfinite_audio_and_traversal(self):
        with self.assertRaises(ValueError):
            self.cache.save("heading", "id", np.array([np.nan]), [], self.audit)
        with self.assertRaises(ValueError):
            self.cache.load("../heading", "id")

    def test_fingerprint_is_order_independent_and_changes_with_inputs(self):
        self.assertEqual(input_fingerprint({"a": 1, "b": "紅"}),
                         input_fingerprint({"b": "紅", "a": 1}))
        self.assertNotEqual(input_fingerprint({"text": "紅"}),
                            input_fingerprint({"text": "红"}))


class InterruptedSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.engine = SimpleNamespace(
            name="fixture", language="zh", synthesis_settings={"model": "pinned"},
            capabilities=TTSCapabilities(False, False, False, False, False),
            post_processing_config=lambda: PostProcessingConfig(False, 0, False),
        )
        self.voice = VoiceSpec("fixture")
        self.chunks = [ChunkInfo("紅樓。"), ChunkInfo("夢醒。")]

    @staticmethod
    def render(**kwargs):
        start = kwargs["prior_audio_frames"]
        audio = np.array([0.1, -0.1, 0.2, -0.2], dtype=np.float32)
        return audio, [WordTimestamp(kwargs["segments"][0].text, start / 24000, (start + 4) / 24000)], \
            ChunkSynthesisAudit(kwargs["chunk_index"], start, start + 4)

    def test_interrupt_reuses_heading_and_completed_body_only(self):
        count = 0

        def interrupt(**kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise KeyboardInterrupt()
            return self.render(**kwargs)

        kwargs = dict(narrator_voice=self.voice, heading_text="紅樓夢。",
                      checkpoint_dir=str(self.root / "section.checkpoints"))
        with patch("openshelf.pipeline.tts._synthesize_segments", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                synthesize_chapter(self.engine, self.chunks, str(self.root / "section.wav"), **kwargs)
        with patch("openshelf.pipeline.tts._synthesize_segments", side_effect=self.render) as render:
            result = synthesize_chapter(self.engine, self.chunks, str(self.root / "section.wav"), **kwargs)
        self.assertEqual(render.call_count, 1)
        self.assertEqual(render.call_args.kwargs["chunk_index"], 1)
        self.assertEqual(result.skipped_chunks, 0)
        self.assertEqual([word.word for words in result.chunk_words for word in words], ["紅樓。", "夢醒。"])
        self.engine.synthesis_settings["model"] = "different"
        with patch("openshelf.pipeline.tts._synthesize_segments", side_effect=self.render) as render:
            synthesize_chapter(self.engine, self.chunks, str(self.root / "section.wav"), **kwargs)
        self.assertEqual(render.call_count, 3)

    def test_completed_section_requires_receipt_and_all_artifact_hashes(self):
        import soundfile as sf

        def encode(wav, m4a, delete_wav=True):
            Path(m4a).write_bytes(b"encoded fixture")
            return sf.info(wav).duration

        args = (self.engine, self.chunks, str(self.root / "section.wav"),
                str(self.root / "section.m4a"), str(self.root / "section.sync.json"),
                str(self.root / "section.synthesis_units.json"), 1, [chunk.text for chunk in self.chunks])
        with patch("openshelf.pipeline.tts._synthesize_segments", side_effect=self.render), \
                patch("openshelf.pipeline.encoder.encode_to_aac", side_effect=encode):
            first, duration = synthesize_chapter_to_files(*args, narrator_voice=self.voice)
        with patch("openshelf.pipeline.tts.synthesize_chapter") as synth, \
                patch("openshelf.pipeline.encoder.audio_duration", return_value=duration):
            reused, _ = synthesize_chapter_to_files(*args, narrator_voice=self.voice, resume=True)
            synth.assert_not_called()
        self.assertEqual(first, reused)
        (self.root / "section.sync.json").write_text("corrupt", encoding="utf-8")
        with patch("openshelf.pipeline.tts._synthesize_segments", side_effect=self.render) as render, \
                patch("openshelf.pipeline.encoder.encode_to_aac", side_effect=encode):
            repaired, _ = synthesize_chapter_to_files(*args, narrator_voice=self.voice, resume=True)
        self.assertEqual(render.call_count, 0)  # Audio regions survived the corrupt final commit.
        self.assertEqual(repaired.skipped_chunks, 0)
