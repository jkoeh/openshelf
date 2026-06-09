"""Tests for the Chatterbox engine adapter."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.engines import create_engine  # noqa: E402
from openshelf.pipeline.engines.chatterbox import (  # noqa: E402
    ChatterboxAdapter,
    _patch_chatterbox_progress_bars,
    _patch_missing_perth_watermarker,
)
from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec  # noqa: E402


class FakeChatterboxRuntime:
    sr = 24000

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return np.asarray([0.0, 0.2, -0.2], dtype=np.float32)


class TestChatterboxAdapter(unittest.TestCase):
    def test_factory_creates_chatterbox_adapter(self):
        engine = create_engine("chatterbox")

        self.assertIsInstance(engine, ChatterboxAdapter)

    def test_available_voices_are_kokoro_bootstrap_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ChatterboxAdapter(voices_dir=tmp)

            voices = engine.available_voices()

        self.assertEqual(voices[0].id, "chatterbox-af_heart")
        self.assertTrue(voices[0].ref_audio_path.endswith(os.path.join("chatterbox", "af_heart.wav")))
        self.assertIn("chatterbox-bm_george", {voice.id for voice in voices})

    def test_synthesize_uses_expression_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, "voice.wav")
            with open(ref_path, "wb") as f:
                f.write(b"RIFF")
            runtime = FakeChatterboxRuntime()
            engine = ChatterboxAdapter(model=runtime)
            segment = DirectedSegment(
                text="Oh dear.",
                original_text="Oh dear.",
                voice=VoiceSpec(
                    id="chatterbox-test",
                    ref_audio_path=ref_path,
                    ref_text="Reference text.",
                ),
                speaker="narrator",
                emotion="anxious",
                engine_controls={"intensity": 1.0},
            )

            directed = engine.apply_performance_controls(segment)
            result = engine.synthesize(directed)

        np.testing.assert_allclose(result.audio, np.asarray([0.0, 0.2, -0.2], dtype=np.float32))
        self.assertEqual(result.sample_rate, 24000)
        self.assertEqual(directed.engine_controls["exaggeration"], 0.78)
        self.assertEqual(directed.engine_controls["cfg_weight"], 0.36)
        self.assertEqual(runtime.calls[0]["text"], "Oh dear.")
        self.assertEqual(runtime.calls[0]["audio_prompt_path"], ref_path)
        self.assertEqual(runtime.calls[0]["exaggeration"], 0.78)
        self.assertEqual(runtime.calls[0]["cfg_weight"], 0.36)

    def test_synthesize_rejects_missing_reference_audio_before_model_load(self):
        runtime = FakeChatterboxRuntime()
        engine = ChatterboxAdapter(model=runtime)
        segment = DirectedSegment(
            text="Hello.",
            voice=VoiceSpec(id="chatterbox-missing", ref_audio_path="missing.wav"),
            speaker="narrator",
        )

        with self.assertRaisesRegex(FileNotFoundError, "Chatterbox reference audio not found"):
            engine.synthesize(segment)

        self.assertEqual(runtime.calls, [])

    def test_runtime_patches_missing_perth_watermarker(self):
        class DummyWatermarker:
            pass

        fake_perth = types.SimpleNamespace(
            PerthImplicitWatermarker=None,
            DummyWatermarker=DummyWatermarker,
        )

        with mock.patch.dict(sys.modules, {"perth": fake_perth}):
            _patch_missing_perth_watermarker()

        self.assertIs(fake_perth.PerthImplicitWatermarker, DummyWatermarker)

    def test_patches_chatterbox_progress_bars(self):
        def noisy_tqdm(iterable, *args, **kwargs):
            raise OSError("bad stderr")

        fake_t3 = types.SimpleNamespace(tqdm=noisy_tqdm)
        fake_flow = types.SimpleNamespace(tqdm=noisy_tqdm)

        with mock.patch.dict(sys.modules, {
            "chatterbox.models.t3.t3": fake_t3,
            "chatterbox.models.s3gen.flow_matching": fake_flow,
        }):
            _patch_chatterbox_progress_bars()

        self.assertEqual(list(fake_t3.tqdm(range(3))), [0, 1, 2])
        self.assertEqual(list(fake_flow.tqdm(range(2))), [0, 1])


if __name__ == "__main__":
    unittest.main()
