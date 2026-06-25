"""Integration-style tests for AudioDirector with fake engines."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.engines.kokoro import KokoroAdapter  # noqa: E402
from openshelf.pipeline.llm import LLMError, StubLLM  # noqa: E402
from openshelf.pipeline.tts_engine import (  # noqa: E402
    AnnotationPromptConfig,
    DirectedSegment,
    EmotionPromptConfig,
    NullAligner,
    PostProcessingConfig,
    RegistryPromptConfig,
    TTSCapabilities,
    TTSResult,
    VoiceSpec,
    WordTimestamp,
)
from openshelf.pipeline.voice_director import (  # noqa: E402
    AudioDirector,
    BookContext,
    CharacterProfile,
    CharacterRegistry,
    ChunkWindow,
)


class FakeEngine:
    name = "fake"
    capabilities = TTSCapabilities(
        emotion_control=False,
        paralinguistic_markers=False,
        speed_control=True,
        provides_timestamps=True,
        voice_cloning=False,
    )

    def __init__(self):
        self.voice_pool = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="holmes", preset_name="bm_george"),
        ]

    def registry_prompt_config(self):
        return RegistryPromptConfig("Voices: narrator, holmes", {})

    def annotation_prompt_config(self):
        return AnnotationPromptConfig("")

    def emotion_prompt_config(self):
        return None

    def post_processing_config(self):
        return PostProcessingConfig(False, 0, False)

    def available_voices(self):
        return list(self.voice_pool)

    def synthesize(self, segment: DirectedSegment):
        words = segment.text.split()
        audio = np.ones(max(1, len(words)) * 100, dtype=np.float32)
        return TTSResult(
            audio=audio,
            sample_rate=24000,
            words=[
                WordTimestamp(
                    word=w,
                    start=i * 100 / 24000,
                    end=(i + 1) * 100 / 24000,
                )
                for i, w in enumerate(words)
            ],
        )


class FakePerformanceEngine(FakeEngine):
    capabilities = TTSCapabilities(
        emotion_control=True,
        paralinguistic_markers=False,
        speed_control=True,
        provides_timestamps=True,
        voice_cloning=False,
        performance_direction=True,
    )

    def emotion_prompt_config(self):
        return EmotionPromptConfig(
            emotion_vocabulary=["neutral", "anxious", "sad"],
            marker_format=None,
            injection_rules="Use safe pacing.",
            speed_labels=["slow", "normal", "fast"],
        )

    def apply_performance_controls(self, segment: DirectedSegment):
        controls = dict(segment.engine_controls)
        controls["applied_emotion"] = segment.emotion
        return DirectedSegment(
            text=segment.text,
            voice=segment.voice,
            speaker=segment.speaker,
            emotion=segment.emotion,
            speed=segment.speed,
            pause_after_ms=segment.pause_after_ms,
            original_text=segment.original_text,
            delivery_type=segment.delivery_type,
            voice_policy=segment.voice_policy,
            join_policy=segment.join_policy,
            engine_controls=controls,
        )


def _registry() -> CharacterRegistry:
    return CharacterRegistry(
        narrator_voice=VoiceSpec(id="narrator", preset_name="af_heart"),
        characters={
            "Sherlock Holmes": CharacterProfile(
                canonical="Sherlock Holmes",
                aliases=["Holmes"],
                description="A detective.",
                voice=VoiceSpec(id="holmes", preset_name="bm_george"),
            ),
        },
    )


class TestAudioDirector(unittest.TestCase):
    def test_kokoro_skips_performance_direction(self):
        text = 'He said, "Come."'
        llm = StubLLM([{"quote_speakers": [
            {"quote_id": 0, "speaker": "Holmes"},
        ]}])
        director = AudioDirector(
            KokoroAdapter(pipeline=object()),
            llm,
            NullAligner(),
            cast_mode="multicast",
        )

        segments = director.direct_chunk(ChunkWindow("", text, ""), _registry())

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(segments[1].text, '"Come."')
        self.assertEqual(segments[1].original_text, '"Come."')
        self.assertIsNone(segments[1].emotion)
        self.assertEqual(segments[1].speed, 1.0)
        self.assertEqual(segments[1].pause_after_ms, 0)

    def test_registry_saved_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = StubLLM([{
                "narrator_voice_id": "narrator",
                "characters": [{
                    "canonical": "Sherlock Holmes",
                    "aliases": ["Holmes"],
                    "description": "A detective.",
                    "gender": "male",
                    "age": "adult",
                    "persona_of": None,
                    "voice_id": "holmes",
                }],
            }])
            director = AudioDirector(FakeEngine(), llm, NullAligner(), build_dir=tmp)
            registry = director.build_registry(BookContext(
                title="Test",
                author="Author",
                language="en",
                opening_text='"Come," said Holmes.',
            ))

            path = os.path.join(tmp, "character_registry.json")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["narrator_voice"]["id"], "narrator")
            self.assertIn("Sherlock Holmes", payload["characters"])
            self.assertEqual(registry.characters["Sherlock Holmes"].voice.id, "holmes")

    def test_voice_override_skips_registry_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = StubLLM([])
            director = AudioDirector(FakeEngine(), llm, NullAligner(), build_dir=tmp)
            voice = VoiceSpec(id="narrator", preset_name="af_heart")

            registry = director.build_registry(
                BookContext(
                    title="Test",
                    author="Author",
                    language="en",
                    opening_text='"Come," said Holmes.',
                ),
                narrator_voice_override=voice,
            )

            path = os.path.join(tmp, "character_registry.json")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(len(llm.calls), 0)
            self.assertEqual(registry.narrator_voice.id, "narrator")
            self.assertEqual(registry.characters, {})
            self.assertEqual(payload["narrator_voice"]["id"], "narrator")
            self.assertEqual(payload["characters"], {})

    def test_fallback_on_llm_error(self):
        text = '"Come," said Holmes.'
        director = AudioDirector(
            FakeEngine(),
            StubLLM([LLMError("nope")]),
            NullAligner(),
            cast_mode="multicast",
        )

        segments = director.direct_chunk(ChunkWindow("", text, ""), _registry())

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "narrator")
        self.assertEqual(segments[0].text, text)

    def test_full_chunk_pipeline(self):
        text = 'He said, "Come."'
        llm = StubLLM([{"quote_speakers": [
            {"quote_id": 0, "speaker": "Holmes"},
        ]}])
        director = AudioDirector(
            FakeEngine(),
            llm,
            NullAligner(),
            sample_rate=24000,
            cast_mode="multicast",
        )

        segments = director.direct_chunk(ChunkWindow("", text, ""), _registry())
        audio, words = director.synthesize_chunk(segments, prior_frames=1200)

        self.assertGreater(len(audio), 0)
        self.assertTrue(all(words[i].start <= words[i + 1].start for i in range(len(words) - 1)))
        self.assertGreaterEqual(words[0].start, 1200 / 24000)
        self.assertLessEqual(words[-1].end, len(audio) / 24000 + 1200 / 24000)

    def test_solo_mode_uses_narrator_without_speaker_llm(self):
        text = 'He said, "Come."'
        llm = StubLLM([])
        director = AudioDirector(FakeEngine(), llm, NullAligner())

        segments = director.direct_chunk(ChunkWindow("", text, ""), _registry())

        self.assertEqual(len(llm.calls), 0)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "narrator")
        self.assertEqual(segments[0].voice.id, "narrator")
        self.assertEqual(segments[0].text, text)

    def test_default_batched_performance_direction_uses_one_chapter_call(self):
        windows = [
            ChunkWindow("", "First chunk.", "Second chunk."),
            ChunkWindow("First chunk.", "Second chunk.", ""),
        ]
        llm = StubLLM([{"chunks": [
            {"chunk_index": 0, "mode": "whole", "emotion": "anxious", "speed": "normal"},
            {"chunk_index": 1, "mode": "whole", "emotion": "sad", "speed": "slow"},
        ]}])
        director = AudioDirector(FakePerformanceEngine(), llm, NullAligner())

        _registry_after, directed = director.direct_chapter("I", windows, _registry())

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(directed[0][0].emotion, "anxious")
        self.assertEqual(directed[1][0].emotion, "sad")
        self.assertEqual(directed[0][0].engine_controls["applied_emotion"], "anxious")
        self.assertIn("chunk_index", llm.calls[0]["user"])

    def test_default_batched_performance_direction_can_split_chunk_units(self):
        text = (
            "Calm narration settles the room with measured detail before the sudden interruption. "
            "\"Then fear!\""
        )
        windows = [ChunkWindow("", text, "")]
        llm = StubLLM([{"chunks": [{
            "chunk_index": 0,
            "mode": "split",
            "units": [
                {
                    "text": "Calm narration settles the room with measured detail before the sudden interruption. ",
                    "emotion": "neutral",
                    "speed": "normal",
                },
                {
                    "text": "\"Then fear!\"",
                    "emotion": "anxious",
                    "speed": "normal",
                    "intensity": 0.8,
                },
            ],
        }]}])
        director = AudioDirector(FakePerformanceEngine(), llm, NullAligner())

        _registry_after, directed = director.direct_chapter("I", windows, _registry())

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(
            [segment.text for segment in directed[0]],
            [
                "Calm narration settles the room with measured detail before the sudden interruption. ",
                "\"Then fear!\"",
            ],
        )
        self.assertEqual("".join(segment.text for segment in directed[0]), windows[0].text)
        self.assertEqual([segment.emotion for segment in directed[0]], ["neutral", "anxious"])
        self.assertEqual(directed[0][1].engine_controls["intensity"], 0.6)
        self.assertEqual(directed[0][1].engine_controls["applied_emotion"], "anxious")

    def test_chunk_performance_direction_preserves_per_chunk_calls(self):
        windows = [
            ChunkWindow("", "First chunk.", "Second chunk."),
            ChunkWindow("First chunk.", "Second chunk.", ""),
        ]
        llm = StubLLM([
            {"annotations": [{"index": 0, "emotion": "anxious", "speed": "normal"}]},
            {"annotations": [{"index": 0, "emotion": "sad", "speed": "slow"}]},
        ])
        director = AudioDirector(
            FakePerformanceEngine(),
            llm,
            NullAligner(),
            performance_direction_mode="chunk",
        )

        _registry_after, directed = director.direct_chapter("I", windows, _registry())

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(directed[0][0].emotion, "anxious")
        self.assertEqual(directed[1][0].emotion, "sad")
        self.assertIn('"index": 0', llm.calls[0]["user"])

    def test_off_performance_direction_uses_neutral_without_llm_calls(self):
        windows = [ChunkWindow("", "Plain narration.", "")]
        llm = StubLLM([])
        director = AudioDirector(
            FakePerformanceEngine(),
            llm,
            NullAligner(),
            performance_direction_mode="off",
        )

        _registry_after, directed = director.direct_chapter("I", windows, _registry())

        self.assertEqual(len(llm.calls), 0)
        self.assertEqual(directed[0][0].emotion, "neutral")
        self.assertEqual(directed[0][0].speed, 0.95)
        self.assertEqual(directed[0][0].engine_controls["intensity"], 0.5)


if __name__ == "__main__":
    unittest.main()
