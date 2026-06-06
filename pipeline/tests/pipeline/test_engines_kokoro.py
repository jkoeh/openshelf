"""Tests for Kokoro engine adapter and segment synthesis."""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.config import TTS_SAMPLE_RATE  # noqa: E402
from openshelf.pipeline.engines.kokoro import KokoroAdapter  # noqa: E402
from openshelf.pipeline.tts import ChunkInfo, _synthesize_segments, _synthesize_single_chunk  # noqa: E402
from openshelf.pipeline.tts_engine import DirectedSegment, NullAligner, VoiceSpec  # noqa: E402


class _FakeToken:
    def __init__(self, text, start_ts, end_ts, whitespace=" "):
        self.text = text
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.whitespace = whitespace


class _FakeResult:
    def __init__(self, audio, tokens):
        self.audio = audio
        self.tokens = tokens


class FakeKPipeline:
    def __init__(self):
        self.calls = []

    def __call__(self, text, voice=None, **kwargs):
        self.calls.append({"text": text, "voice": voice, **kwargs})
        words = text.split()
        n_samples = max(1, len(words)) * 100
        audio = np.ones(n_samples, dtype=np.float32) * 0.5
        tokens = [
            _FakeToken(
                word,
                i * 100 / TTS_SAMPLE_RATE,
                (i + 1) * 100 / TTS_SAMPLE_RATE,
            )
            for i, word in enumerate(words)
        ]
        return iter([_FakeResult(audio, tokens)])


def _segment(text: str, voice_id: str) -> DirectedSegment:
    return DirectedSegment(
        text=text,
        voice=VoiceSpec(id=voice_id, preset_name=voice_id),
        speaker=voice_id,
    )


class TestKokoroSegmentSynthesis(unittest.TestCase):
    def test_kokoro_does_not_advertise_llm_performance_direction(self):
        engine = KokoroAdapter(pipeline=FakeKPipeline())

        self.assertFalse(engine.capabilities.emotion_control)
        self.assertFalse(engine.capabilities.performance_direction)
        self.assertIsNone(engine.emotion_prompt_config())

    def test_concat_length(self):
        pipeline = FakeKPipeline()
        engine = KokoroAdapter(pipeline=pipeline)
        post_cfg = engine.post_processing_config()
        segments = [_segment("one two", "af_heart"), _segment("three", "bm_george")]

        audio, _ = _synthesize_segments(
            engine,
            NullAligner(),
            segments,
            post_cfg,
            TTS_SAMPLE_RATE,
            prior_audio_frames=0,
        )

        transition = int(TTS_SAMPLE_RATE * post_cfg.voice_transition_silence_ms / 1000)
        self.assertEqual(len(audio), 200 + transition + 100)

    def test_kokoro_native_words_not_used_for_final_sync(self):
        pipeline = FakeKPipeline()
        engine = KokoroAdapter(pipeline=pipeline)
        post_cfg = engine.post_processing_config()

        _, words = _synthesize_segments(
            engine,
            NullAligner(),
            [_segment("one two", "af_heart"), _segment("three", "bm_george")],
            post_cfg,
            TTS_SAMPLE_RATE,
            prior_audio_frames=0,
        )

        self.assertTrue(post_cfg.needs_forced_alignment)
        self.assertEqual(words, [])

    def test_single_segment_matches_legacy_contract(self):
        pipeline_a = FakeKPipeline()
        legacy_audio, legacy_words = _synthesize_single_chunk(
            pipeline_a,
            ChunkInfo("one two"),
            "af_heart",
            TTS_SAMPLE_RATE,
        )

        pipeline_b = FakeKPipeline()
        engine = KokoroAdapter(pipeline=pipeline_b)
        direct_audio, direct_words = _synthesize_segments(
            engine,
            NullAligner(),
            [_segment("one two", "af_heart")],
            engine.post_processing_config(),
            TTS_SAMPLE_RATE,
            prior_audio_frames=0,
        )

        self.assertEqual(len(legacy_audio), len(direct_audio))
        self.assertEqual([w.word for w in legacy_words], [w.word for w in direct_words])
        self.assertEqual(pipeline_a.calls[0]["voice"], "af_heart")

    def test_voice_transition_silence_inserted(self):
        pipeline = FakeKPipeline()
        engine = KokoroAdapter(pipeline=pipeline)
        post_cfg = engine.post_processing_config()
        audio, _ = _synthesize_segments(
            engine,
            NullAligner(),
            [_segment("one", "af_heart"), _segment("two", "bm_george")],
            post_cfg,
            TTS_SAMPLE_RATE,
            prior_audio_frames=0,
        )

        transition = int(TTS_SAMPLE_RATE * post_cfg.voice_transition_silence_ms / 1000)
        self.assertEqual(len(audio), 100 + transition + 100)

    def test_tight_join_suppresses_voice_transition_silence(self):
        pipeline = FakeKPipeline()
        engine = KokoroAdapter(pipeline=pipeline)
        audio, _ = _synthesize_segments(
            engine,
            NullAligner(),
            [
                _segment("one", "af_heart"),
                DirectedSegment(
                    text="two",
                    voice=VoiceSpec(id="bm_george", preset_name="bm_george"),
                    speaker="narrator",
                    join_policy="tight",
                ),
            ],
            engine.post_processing_config(),
            TTS_SAMPLE_RATE,
            prior_audio_frames=0,
        )

        self.assertEqual(len(audio), 200)

    def test_same_voice_no_silence(self):
        pipeline = FakeKPipeline()
        engine = KokoroAdapter(pipeline=pipeline)
        audio, _ = _synthesize_segments(
            engine,
            NullAligner(),
            [_segment("one", "af_heart"), _segment("two", "af_heart")],
            engine.post_processing_config(),
            TTS_SAMPLE_RATE,
            prior_audio_frames=0,
        )

        self.assertEqual(len(audio), 200)


if __name__ == "__main__":
    unittest.main()
