"""Tests for F5-TTS Kokoro-bootstrap voice catalog."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.engines.f5tts import (  # noqa: E402
    DEFAULT_REF_TEXT,
    F5TTSAdapter,
    bootstrap_kokoro_reference_voices,
)
from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec  # noqa: E402


class _FakeResult:
    def __init__(self, audio):
        self.audio = audio


class FakeKPipeline:
    def __init__(self):
        self.calls = []

    def __call__(self, text, voice=None, **kwargs):
        self.calls.append({"text": text, "voice": voice, **kwargs})
        return iter([_FakeResult(np.ones(256, dtype=np.float32) * 0.25)])


class FakeF5Runtime:
    def __init__(self, wav=None, sample_rate=24000):
        self.calls = []
        self.wav = np.asarray(wav if wav is not None else [0.0, 0.25, -0.25], dtype=np.float32)
        self.sample_rate = sample_rate

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return self.wav, self.sample_rate, object()


class TestF5TTSAdapter(unittest.TestCase):
    def test_available_voices_are_kokoro_bootstrap_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = F5TTSAdapter(voices_dir=tmp)

            voices = engine.available_voices()

        self.assertEqual(voices[0].id, "f5tts-af_heart")
        self.assertTrue(voices[0].ref_audio_path.endswith(os.path.join("f5tts", "af_heart.wav")))
        self.assertEqual(voices[0].ref_text, DEFAULT_REF_TEXT)
        self.assertIn("f5tts-bm_george", {voice.id for voice in voices})

    def test_registry_prompt_preserves_kokoro_casting_guidance_with_f5_ids(self):
        prompt = F5TTSAdapter().registry_prompt_config().voice_pool_description

        self.assertIn("f5tts-af_heart: warm, friendly", prompt)
        self.assertIn("f5tts-bf_alice", prompt)
        self.assertIn("whimsical children's fiction", prompt)
        self.assertIn("local reference clip", prompt)

    def test_bootstrap_writes_reference_clip_for_selected_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = FakeKPipeline()

            results = bootstrap_kokoro_reference_voices(
                voices_dir=tmp,
                voice_ids=["af_heart"],
                kokoro_pipeline=pipeline,
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "written")
            self.assertEqual(results[0].voice_id, "f5tts-af_heart")
            self.assertTrue(os.path.exists(results[0].path))
            self.assertEqual(pipeline.calls[0]["voice"], "af_heart")
            self.assertEqual(pipeline.calls[0]["text"], DEFAULT_REF_TEXT)

    def test_bootstrap_skips_existing_reference_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_pipeline = FakeKPipeline()
            bootstrap_kokoro_reference_voices(
                voices_dir=tmp,
                voice_ids=["f5tts-af_heart"],
                kokoro_pipeline=first_pipeline,
            )
            second_pipeline = FakeKPipeline()

            results = bootstrap_kokoro_reference_voices(
                voices_dir=tmp,
                voice_ids=["af_heart"],
                kokoro_pipeline=second_pipeline,
            )

            self.assertEqual(results[0].status, "exists")
            self.assertEqual(second_pipeline.calls, [])

    def test_bootstrap_dry_run_does_not_load_kokoro(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = FakeKPipeline()

            results = bootstrap_kokoro_reference_voices(
                voices_dir=tmp,
                voice_ids=["af_heart"],
                dry_run=True,
                kokoro_pipeline=pipeline,
            )

            self.assertEqual(results[0].status, "would_write")
            self.assertEqual(pipeline.calls, [])
            self.assertFalse(os.path.exists(os.path.join(tmp, "f5tts")))

    def test_bootstrap_rejects_unknown_voice_id(self):
        with self.assertRaises(ValueError):
            bootstrap_kokoro_reference_voices(voice_ids=["not-a-voice"], dry_run=True)

    def test_synthesize_uses_f5_runtime_with_reference_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, "voice.wav")
            with open(ref_path, "wb") as f:
                f.write(b"RIFF")
            runtime = FakeF5Runtime()
            engine = F5TTSAdapter(f5tts=runtime, seed=123)
            segment = DirectedSegment(
                text="Hello there.",
                original_text="Hello there.",
                voice=VoiceSpec(
                    id="f5tts-test",
                    ref_audio_path=ref_path,
                    ref_text="Reference text.",
                ),
                speaker="narrator",
                speed=0.95,
            )

            result = engine.synthesize(segment)

        self.assertEqual(result.sample_rate, 24000)
        np.testing.assert_allclose(result.audio, np.asarray([0.0, 0.25, -0.25], dtype=np.float32))
        self.assertIsNone(result.words)
        self.assertEqual(runtime.calls[0]["ref_file"], ref_path)
        self.assertEqual(runtime.calls[0]["ref_text"], "Reference text.")
        self.assertEqual(runtime.calls[0]["gen_text"], "Hello there.")
        self.assertEqual(runtime.calls[0]["speed"], 0.95)
        self.assertEqual(runtime.calls[0]["seed"], 123)
        self.assertFalse(runtime.calls[0]["remove_silence"])

    def test_synthesize_rejects_missing_reference_audio_before_model_load(self):
        runtime = FakeF5Runtime()
        engine = F5TTSAdapter(f5tts=runtime)
        segment = DirectedSegment(
            text="Hello.",
            voice=VoiceSpec(id="f5tts-missing", ref_audio_path="missing.wav", ref_text="Reference."),
            speaker="narrator",
        )

        with self.assertRaisesRegex(FileNotFoundError, "F5-TTS reference audio not found"):
            engine.synthesize(segment)

        self.assertEqual(runtime.calls, [])

    def test_synthesize_requires_reference_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, "voice.wav")
            with open(ref_path, "wb") as f:
                f.write(b"RIFF")
            engine = F5TTSAdapter(f5tts=FakeF5Runtime())
            segment = DirectedSegment(
                text="Hello.",
                voice=VoiceSpec(id="f5tts-missing-text", ref_audio_path=ref_path),
                speaker="narrator",
            )

            with self.assertRaisesRegex(ValueError, "requires reference text"):
                engine.synthesize(segment)

    def test_synthesize_wraps_missing_f5_package_with_install_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, "voice.wav")
            with open(ref_path, "wb") as f:
                f.write(b"RIFF")
            engine = F5TTSAdapter()
            segment = DirectedSegment(
                text="Hello.",
                voice=VoiceSpec(id="f5tts-test", ref_audio_path=ref_path, ref_text="Reference."),
                speaker="narrator",
            )

            with patch("builtins.__import__", side_effect=ImportError("missing")):
                with self.assertRaisesRegex(RuntimeError, "pip install f5-tts"):
                    engine.synthesize(segment)

    def test_f5_dependency_declared(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        with open(os.path.join(root, "pipeline", "requirements.txt"), "r", encoding="utf-8") as f:
            requirements = f.read()
        with open(os.path.join(root, "pipeline", "pyproject.toml"), "r", encoding="utf-8") as f:
            pyproject = f.read()

        self.assertIn("f5-tts", requirements)
        self.assertIn('"f5-tts"', pyproject)

    def test_post_processing_uses_tight_stitching_for_cloned_segments(self):
        cfg = F5TTSAdapter().post_processing_config()

        self.assertEqual(cfg.voice_transition_silence_ms, 0)
        self.assertEqual(cfg.max_generated_boundary_silence_ms, 25)
        self.assertTrue(cfg.needs_forced_alignment)


if __name__ == "__main__":
    unittest.main()
