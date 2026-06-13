"""Tests for the Chatterbox engine adapter."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.engines import create_engine  # noqa: E402
from openshelf.pipeline.engines.chatterbox import (  # noqa: E402
    ChatterboxAdapter,
    _patch_chatterbox_backend_forward,
    _patch_chatterbox_progress_bars,
    _patch_missing_perth_watermarker,
)
from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec  # noqa: E402


class FakeChatterboxRuntime:
    sr = 24000

    def __init__(self):
        self.calls = []
        self.prepare_calls = []

    def prepare_conditionals(self, audio_prompt_path, exaggeration=0.5):
        self.prepare_calls.append({
            "audio_prompt_path": audio_prompt_path,
            "exaggeration": exaggeration,
        })

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
        self.assertEqual(runtime.prepare_calls[0]["audio_prompt_path"], ref_path)
        self.assertEqual(runtime.prepare_calls[0]["exaggeration"], 0.78)
        self.assertEqual(runtime.calls[0]["text"], "Oh dear.")
        self.assertNotIn("audio_prompt_path", runtime.calls[0])
        self.assertEqual(runtime.calls[0]["exaggeration"], 0.78)
        self.assertEqual(runtime.calls[0]["cfg_weight"], 0.36)

    def test_synthesize_reuses_prepared_reference_for_same_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref_path = os.path.join(tmp, "voice.wav")
            with open(ref_path, "wb") as f:
                f.write(b"RIFF")
            runtime = FakeChatterboxRuntime()
            engine = ChatterboxAdapter(model=runtime)
            voice = VoiceSpec(
                id="chatterbox-test",
                ref_audio_path=ref_path,
                ref_text="Reference text.",
            )
            first = DirectedSegment(
                text="First sentence.",
                original_text="First sentence.",
                voice=voice,
                speaker="narrator",
                emotion="neutral",
            )
            second = DirectedSegment(
                text="Second sentence.",
                original_text="Second sentence.",
                voice=voice,
                speaker="narrator",
                emotion="sad",
                engine_controls={"intensity": 1.0},
            )

            engine.synthesize(first)
            engine.synthesize(second)

        self.assertEqual(len(runtime.prepare_calls), 1)
        self.assertEqual(len(runtime.calls), 2)
        self.assertNotIn("audio_prompt_path", runtime.calls[0])
        self.assertNotIn("audio_prompt_path", runtime.calls[1])
        self.assertEqual(runtime.calls[1]["exaggeration"], 0.38)
        self.assertEqual(runtime.calls[1]["cfg_weight"], 0.58)

    @mock.patch.dict(os.environ, {
        "OPENSHELF_CHATTERBOX_MAX_CHARS": "80",
        "OPENSHELF_CHATTERBOX_SPLIT_PAUSE_MS": "12",
    })
    def test_split_synthesis_units_packs_long_text(self):
        engine = ChatterboxAdapter()
        text = (
            "First sentence has enough words to be useful for packing. "
            "Second sentence should fit alongside the first when allowed. "
            "Third sentence forces another generated unit."
        )

        units = engine.split_synthesis_units(text)

        self.assertGreater(len(units), 1)
        self.assertTrue(all(len(unit) <= 80 for unit, _pause in units))
        self.assertTrue(all(pause == 12 for _unit, pause in units[:-1]))
        self.assertEqual(units[-1][1], 0)
        self.assertEqual(" ".join(unit for unit, _pause in units), text)

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

    def test_patches_chatterbox_backend_forward_to_skip_attention_outputs(self):
        class FakeModel:
            def __init__(self):
                self.kwargs = None

            def __call__(self, **kwargs):
                self.kwargs = kwargs
                return types.SimpleNamespace(
                    last_hidden_state=torch.ones(1, 1, 2),
                    past_key_values=("past",),
                )

        class FakeBackend:
            def __init__(self):
                self.model = FakeModel()
                self.speech_head = lambda hidden: hidden + 1

        fake_backend_module = types.SimpleNamespace(T3HuggingfaceBackend=FakeBackend)

        with mock.patch.dict(sys.modules, {
            "chatterbox.models.t3.inference.t3_hf_backend": fake_backend_module,
        }):
            _patch_chatterbox_backend_forward()

        backend = FakeBackend()
        output = backend.forward(
            inputs_embeds=torch.ones(1, 2, 2),
            output_attentions=True,
            output_hidden_states=True,
        )

        self.assertTrue(FakeBackend._openshelf_fast_forward)
        self.assertFalse(backend.model.kwargs["output_attentions"])
        self.assertFalse(backend.model.kwargs["output_hidden_states"])
        self.assertIsNone(output.attentions)
        self.assertEqual(tuple(output.logits.shape), (1, 1, 2))


if __name__ == "__main__":
    unittest.main()
