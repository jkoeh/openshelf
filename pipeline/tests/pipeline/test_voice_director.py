"""Tests for voice_director.py."""

from __future__ import annotations

import os
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.llm import LLMError, StubLLM  # noqa: E402
from openshelf.pipeline.tts_engine import (  # noqa: E402
    AnnotationPromptConfig,
    DirectedSegment,
    EmotionPromptConfig,
    VoiceSpec,
)
from openshelf.pipeline.voice_director import (  # noqa: E402
    BookContext,
    ChapterWindow,
    CharacterProfile,
    CharacterRegistry,
    ChunkWindow,
    SpeakerSpan,
    add_emotion_direction,
    annotate_chapter_speakers,
    annotate_with_fallback,
    assign_voices,
    build_character_registry,
    build_direction_chapter,
    build_voice_direction_payload,
    directed_chunks_from_chapter_direction_artifact,
    directed_segment_to_dict,
    extract_quote_spans,
    group_performance_units,
    merge_adjacent_segments,
    merge_new_characters,
    needs_annotation,
    quote_speaker_assignments_to_spans,
    resolve_profile,
    resolve_voice,
    spans_to_segments,
    validate_spans,
    validate_span_speakers,
)


def _registry() -> CharacterRegistry:
    narrator = VoiceSpec(id="narrator", preset_name="af_heart")
    holmes = VoiceSpec(id="holmes", preset_name="bm_george")
    watson = VoiceSpec(id="watson", preset_name="am_eric")
    return CharacterRegistry(
        narrator_voice=narrator,
        characters={
            "Sherlock Holmes": CharacterProfile(
                canonical="Sherlock Holmes",
                aliases=["Holmes", "the detective"],
                description="A precise detective.",
                gender="male",
                voice=holmes,
            ),
            "Dr. Watson": CharacterProfile(
                canonical="Dr. Watson",
                aliases=["Watson", "doctor"],
                description="A warm doctor.",
                gender="male",
                voice=watson,
            ),
            "Disguised Man": CharacterProfile(
                canonical="Disguised Man",
                aliases=["stranger"],
                description="A disguise.",
                persona_of="Sherlock Holmes",
                voice=VoiceSpec(id="disguise", preset_name="am_adam"),
            ),
        },
    )


class TestNeedsAnnotation(unittest.TestCase):
    def test_apostrophe_only_is_false(self):
        self.assertFalse(needs_annotation("I don't know."))

    def test_straight_quote_is_true(self):
        self.assertTrue(needs_annotation('"I know," he said.'))

    def test_curly_quote_is_true(self):
        self.assertTrue(needs_annotation("\u201cI know,\u201d he said."))


class TestValidateSpans(unittest.TestCase):
    def test_perfect_tiling_passes(self):
        validate_spans([
            SpeakerSpan(0, 2, "narrator"),
            SpeakerSpan(2, 5, "Sherlock Holmes"),
        ], "abcde")

    def test_gap_fails(self):
        with self.assertRaises(ValueError):
            validate_spans([SpeakerSpan(0, 1, "n"), SpeakerSpan(2, 3, "n")], "abc")

    def test_overlap_fails(self):
        with self.assertRaises(ValueError):
            validate_spans([SpeakerSpan(0, 2, "n"), SpeakerSpan(1, 3, "n")], "abc")

    def test_oob_fails(self):
        with self.assertRaises(ValueError):
            validate_spans([SpeakerSpan(0, 4, "n")], "abc")

    def test_unsorted_fails(self):
        with self.assertRaises(ValueError):
            validate_spans([SpeakerSpan(2, 3, "n"), SpeakerSpan(0, 2, "n")], "abc")

    def test_empty_fails_for_nonempty_text(self):
        with self.assertRaises(ValueError):
            validate_spans([], "abc")

    def test_random_valid_tilings_pass(self):
        random.seed(42)
        text = "abcdefghijklmnopqrstuvwxyz"
        for _ in range(50):
            cuts = sorted(random.sample(range(1, len(text)), 5))
            bounds = [0, *cuts, len(text)]
            spans = [
                SpeakerSpan(bounds[i], bounds[i + 1], "narrator")
                for i in range(len(bounds) - 1)
            ]
            validate_spans(spans, text)
            self.assertEqual(
                "".join(text[s.start:s.end] for s in spans),
                text,
            )

    def test_unknown_speaker_fails_speaker_validation(self):
        with self.assertRaises(ValueError):
            validate_span_speakers([
                SpeakerSpan(0, 5, "Moriarty"),
            ], _registry())


class TestVoiceResolution(unittest.TestCase):
    def test_alias_resolves_to_character_voice(self):
        voice = resolve_voice("the detective", _registry())
        self.assertEqual(voice.preset_name, "bm_george")

    def test_unknown_resolves_to_narrator(self):
        voice = resolve_voice("Nobody", _registry())
        self.assertEqual(voice.preset_name, "af_heart")

    def test_persona_redirects_to_parent_voice(self):
        voice = resolve_voice("stranger", _registry())
        self.assertEqual(voice.preset_name, "bm_george")

    def test_duplicate_alias_is_ambiguous(self):
        voice = VoiceSpec(id="voice", preset_name="af_heart")
        registry = CharacterRegistry(
            narrator_voice=voice,
            characters={
                "John (sailor)": CharacterProfile(
                    canonical="John (sailor)",
                    aliases=["John"],
                    description="A sailor.",
                    voice=voice,
                ),
                "John (butler)": CharacterProfile(
                    canonical="John (butler)",
                    aliases=["John"],
                    description="A butler.",
                    voice=voice,
                ),
            },
        )

        self.assertIsNone(resolve_profile("John", registry))
        self.assertEqual(resolve_profile("John (sailor)", registry).canonical, "John (sailor)")
        with self.assertRaises(ValueError):
            validate_span_speakers([SpeakerSpan(0, 4, "John")], registry)


class TestSegments(unittest.TestCase):
    def test_extract_quote_spans_straight_and_curly(self):
        text = 'A "one" and \u201ctwo\u201d.'
        spans = extract_quote_spans(text)
        self.assertEqual([(s.start, s.end, s.text) for s in spans], [
            (2, 7, '"one"'),
            (12, 17, "\u201ctwo\u201d"),
        ])

    def test_spans_to_segments_preserves_text(self):
        text = 'He said, "Come."'
        spans = [
            SpeakerSpan(0, 9, "narrator"),
            SpeakerSpan(9, len(text), "Sherlock Holmes"),
        ]
        segments = spans_to_segments(spans, text, _registry())
        self.assertEqual("".join(s.text for s in segments), text)
        self.assertEqual(segments[1].speaker, "Sherlock Holmes")

    def test_spans_to_segments_rejects_unknown_speaker(self):
        text = '"Come."'
        with self.assertRaises(ValueError):
            spans_to_segments([
                SpeakerSpan(0, len(text), "Moriarty"),
            ], text, _registry())

    def test_merge_adjacent_same_voice(self):
        registry = _registry()
        text = "abcdef"
        segments = spans_to_segments([
            SpeakerSpan(0, 2, "narrator"),
            SpeakerSpan(2, 4, "narrator"),
            SpeakerSpan(4, 6, "Watson"),
        ], text, registry)
        merged = merge_adjacent_segments(segments)
        self.assertEqual([s.text for s in merged], ["abcd", "ef"])

    def test_merge_preserves_alternating(self):
        registry = _registry()
        text = "abcdef"
        segments = spans_to_segments([
            SpeakerSpan(0, 2, "narrator"),
            SpeakerSpan(2, 4, "Watson"),
            SpeakerSpan(4, 6, "narrator"),
        ], text, registry)
        merged = merge_adjacent_segments(segments)
        self.assertEqual([s.text for s in merged], ["ab", "cd", "ef"])

    def test_quote_assignments_tile_text(self):
        text = 'He said, "Come." Then left.'
        quote_spans = extract_quote_spans(text)
        spans = quote_speaker_assignments_to_spans(
            quote_spans,
            {0: "Holmes"},
            text,
        )
        self.assertEqual([(s.start, s.end, s.speaker) for s in spans], [
            (0, 9, "narrator"),
            (9, 16, "Holmes"),
            (16, len(text), "narrator"),
        ])

    def test_internal_thought_and_tag_group_as_narrator_compatible_unit(self):
        registry = CharacterRegistry(
            narrator_voice=VoiceSpec(id="narrator", preset_name="bf_alice"),
            characters={
                "Alice": CharacterProfile(
                    canonical="Alice",
                    aliases=["Alice"],
                    description="Curious child.",
                    voice=VoiceSpec(id="alice", preset_name="af_heart"),
                ),
            },
        )
        text = '"I am not Ada," she said, "for her hair goes in ringlets."'
        first, second = extract_quote_spans(text)
        spans = [
            SpeakerSpan(
                first.start,
                first.end,
                "Alice",
                delivery_type="self_talk",
                voice_policy="narrator_compatible",
            ),
            SpeakerSpan(first.end, second.start, "narrator"),
            SpeakerSpan(
                second.start,
                second.end,
                "Alice",
                delivery_type="self_talk",
                voice_policy="narrator_compatible",
            ),
        ]

        segments = group_performance_units(spans, text, registry)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].voice.id, "narrator")
        self.assertEqual(segments[0].speaker, "Alice")
        self.assertEqual(segments[0].voice_policy, "narrator_compatible")
        self.assertEqual(segments[0].original_text, text)

    def test_spoken_dialogue_keeps_character_voice_and_tight_tag_join(self):
        registry = CharacterRegistry(
            narrator_voice=VoiceSpec(id="narrator", preset_name="bf_alice"),
            characters={
                "White Rabbit": CharacterProfile(
                    canonical="White Rabbit",
                    aliases=["Rabbit"],
                    description="Hurried rabbit.",
                    voice=VoiceSpec(id="rabbit", preset_name="am_echo"),
                ),
            },
        )
        text = '"Oh dear!" said the Rabbit.'
        quote = extract_quote_spans(text)[0]
        spans = [
            SpeakerSpan(
                quote.start,
                quote.end,
                "White Rabbit",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(quote.end, len(text), "narrator"),
        ]

        segments = group_performance_units(spans, text, registry)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].voice.id, "rabbit")
        self.assertEqual(segments[0].speaker, "White Rabbit")
        self.assertEqual(segments[1].speaker, "narrator")
        self.assertEqual(segments[1].join_policy, "tight")
        self.assertEqual(
            "".join(segment.original_text or segment.text for segment in segments),
            text,
        )

    def test_main_pov_spoken_line_without_exchange_uses_narrator_compatible_voice(self):
        registry = CharacterRegistry(
            narrator_voice=VoiceSpec(id="narrator", preset_name="bf_alice"),
            characters={
                "Alice": CharacterProfile(
                    canonical="Alice",
                    aliases=["Alice"],
                    description="The protagonist and main POV child.",
                    voice=VoiceSpec(id="alice", preset_name="af_bella"),
                ),
            },
        )
        text = '"Curiouser and curiouser!" cried Alice.'
        quote = extract_quote_spans(text)[0]
        spans = [
            SpeakerSpan(
                quote.start,
                quote.end,
                "Alice",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(quote.end, len(text), "narrator"),
        ]

        segments = group_performance_units(spans, text, registry)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "Alice")
        self.assertEqual(segments[0].voice.id, "narrator")
        self.assertEqual(segments[0].voice_policy, "narrator_compatible")
        self.assertEqual(segments[0].original_text, text)

    def test_main_pov_in_dialogue_exchange_can_keep_character_voice(self):
        registry = CharacterRegistry(
            narrator_voice=VoiceSpec(id="narrator", preset_name="bf_alice"),
            characters={
                "Alice": CharacterProfile(
                    canonical="Alice",
                    aliases=["Alice"],
                    description="The protagonist and main POV child.",
                    voice=VoiceSpec(id="alice", preset_name="af_bella"),
                ),
                "Duchess": CharacterProfile(
                    canonical="Duchess",
                    aliases=["the Duchess"],
                    description="A distinct adult character.",
                    voice=VoiceSpec(id="duchess", preset_name="af_heart"),
                ),
            },
        )
        text = '"Please," said Alice. "No," said the Duchess.'
        first, second = extract_quote_spans(text)
        spans = [
            SpeakerSpan(
                first.start,
                first.end,
                "Alice",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(first.end, second.start, "narrator"),
            SpeakerSpan(
                second.start,
                second.end,
                "Duchess",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(second.end, len(text), "narrator"),
        ]

        segments = group_performance_units(spans, text, registry)

        self.assertEqual(segments[0].speaker, "Alice")
        self.assertEqual(segments[0].voice.id, "alice")
        self.assertEqual(segments[1].speaker, "narrator")
        self.assertEqual(segments[1].join_policy, "tight")
        self.assertEqual(segments[2].speaker, "Duchess")
        self.assertEqual(segments[2].voice.id, "duchess")
        self.assertEqual(
            "".join(segment.original_text or segment.text for segment in segments),
            text,
        )

    def test_main_pov_split_line_before_other_speaker_counts_as_exchange(self):
        registry = CharacterRegistry(
            narrator_voice=VoiceSpec(id="narrator", preset_name="bf_alice"),
            characters={
                "Alice": CharacterProfile(
                    canonical="Alice",
                    aliases=["Alice"],
                    description="The protagonist and main POV child.",
                    voice=VoiceSpec(id="alice", preset_name="af_bella"),
                ),
                "Duchess": CharacterProfile(
                    canonical="Duchess",
                    aliases=["the Duchess"],
                    description="A distinct adult character.",
                    voice=VoiceSpec(id="duchess", preset_name="af_heart"),
                ),
            },
        )
        text = '"Somebody said," Alice whispered, "that it is done." "Nonsense," said the Duchess.'
        first, second, third = extract_quote_spans(text)
        spans = [
            SpeakerSpan(
                first.start,
                first.end,
                "Alice",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(first.end, second.start, "narrator"),
            SpeakerSpan(
                second.start,
                second.end,
                "Alice",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(second.end, third.start, "narrator"),
            SpeakerSpan(
                third.start,
                third.end,
                "Duchess",
                delivery_type="spoken_dialogue",
                voice_policy="distinct_character",
            ),
            SpeakerSpan(third.end, len(text), "narrator"),
        ]

        segments = group_performance_units(spans, text, registry)

        self.assertEqual(segments[0].speaker, "Alice")
        self.assertEqual(segments[0].voice.id, "alice")
        self.assertEqual(segments[2].speaker, "Alice")
        self.assertEqual(segments[2].voice.id, "alice")
        self.assertEqual(segments[4].speaker, "Duchess")
        self.assertEqual(
            "".join(segment.original_text or segment.text for segment in segments),
            text,
        )


class TestPerformanceDirection(unittest.TestCase):
    def test_speed_labels_are_audiobook_safe(self):
        cfg = EmotionPromptConfig(
            emotion_vocabulary=["neutral"],
            marker_format=None,
            injection_rules="Use safe pacing.",
            speed_labels=["slow", "normal", "fast"],
        )
        segment = DirectedSegment(
            text='"Run!"',
            voice=VoiceSpec(id="holmes"),
            speaker="Sherlock Holmes",
            original_text='"Run!"',
        )
        llm = StubLLM([{"annotations": [{
            "index": 0,
            "emotion": "neutral",
            "speed": "fast",
        }]}])

        directed = add_emotion_direction(
            [segment],
            ChunkWindow("", '"Run!"', ""),
            llm,
            cfg,
        )

        self.assertEqual(directed[0].speed, 1.05)

    def test_long_narrator_fast_is_capped_to_normal_audiobook_speed(self):
        cfg = EmotionPromptConfig(
            emotion_vocabulary=["neutral"],
            marker_format=None,
            injection_rules="Use safe pacing.",
            speed_labels=["slow", "normal", "fast"],
        )
        text = " ".join(["narration"] * 30)
        segment = DirectedSegment(
            text=text,
            voice=VoiceSpec(id="narrator"),
            speaker="narrator",
            original_text=text,
        )
        llm = StubLLM([{"annotations": [{
            "index": 0,
            "emotion": "neutral",
            "speed": "fast",
        }]}])

        directed = add_emotion_direction(
            [segment],
            ChunkWindow("", text, ""),
            llm,
            cfg,
        )

        self.assertEqual(directed[0].speed, 0.95)

    def test_short_narrator_fast_never_exceeds_one_x(self):
        cfg = EmotionPromptConfig(
            emotion_vocabulary=["neutral"],
            marker_format=None,
            injection_rules="Use safe pacing.",
            speed_labels=["slow", "normal", "fast"],
        )
        segment = DirectedSegment(
            text="Suddenly.",
            voice=VoiceSpec(id="narrator"),
            speaker="narrator",
            original_text="Suddenly.",
        )
        llm = StubLLM([{"annotations": [{
            "index": 0,
            "emotion": "neutral",
            "speed": "fast",
        }]}])

        directed = add_emotion_direction(
            [segment],
            ChunkWindow("", "Suddenly.", ""),
            llm,
            cfg,
        )

        self.assertEqual(directed[0].speed, 1.0)

    def test_narrator_compatible_internal_thought_suppresses_llm_pause(self):
        cfg = EmotionPromptConfig(
            emotion_vocabulary=["neutral"],
            marker_format=None,
            injection_rules="Use safe pacing.",
            speed_labels=["slow", "normal", "fast"],
        )
        segment = DirectedSegment(
            text='"What is the use?" thought Alice.',
            voice=VoiceSpec(id="narrator"),
            speaker="Alice",
            original_text='"What is the use?" thought Alice.',
            delivery_type="internal_thought",
            voice_policy="narrator_compatible",
        )
        llm = StubLLM([{"annotations": [{
            "index": 0,
            "emotion": "neutral",
            "speed": "normal",
            "pause_after_ms": 600,
        }]}])

        directed = add_emotion_direction(
            [segment],
            ChunkWindow("", segment.text, ""),
            llm,
            cfg,
        )

        self.assertEqual(directed[0].pause_after_ms, 0)

    def test_llm_pause_is_capped_for_real_breaks(self):
        cfg = EmotionPromptConfig(
            emotion_vocabulary=["neutral"],
            marker_format=None,
            injection_rules="Use safe pacing.",
            speed_labels=["slow", "normal", "fast"],
        )
        segment = DirectedSegment(
            text="Down, down, down.",
            voice=VoiceSpec(id="narrator"),
            speaker="narrator",
            original_text="Down, down, down.",
            delivery_type="narration",
            voice_policy="narrator",
        )
        llm = StubLLM([{"annotations": [{
            "index": 0,
            "emotion": "neutral",
            "speed": "slow",
            "pause_after_ms": 600,
        }]}])

        directed = add_emotion_direction(
            [segment],
            ChunkWindow("", segment.text, ""),
            llm,
            cfg,
        )

        self.assertEqual(directed[0].pause_after_ms, 120)


class TestBuildCharacterRegistry(unittest.TestCase):
    def test_retries_malformed_registry_response(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="alice", preset_name="bf_alice"),
        ]
        llm = StubLLM([
            LLMError("invalid json"),
            {
                "narrator_voice_id": "narrator",
                "characters": [{
                    "canonical": "Alice",
                    "aliases": ["Alice"],
                    "description": "A curious child speaking to herself.",
                    "gender": "female",
                    "age": "child",
                    "persona_of": None,
                    "voice_id": "alice",
                    "first_evidence_quote": "what is the use of a book",
                }],
            },
        ])

        registry = build_character_registry(
            BookContext(
                title="Alice",
                author="Lewis Carroll",
                language="en",
                opening_text='"what is the use of a book," thought Alice',
            ),
            llm,
            prompt_cfg=type("Cfg", (), {
                "voice_pool_description": "Voices: narrator, alice",
            })(),
            voice_pool=voices,
        )

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Alice", registry.characters)

    def test_registry_filters_narrator_character(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="alice", preset_name="bf_alice"),
        ]
        llm = StubLLM([{
            "narrator_voice_id": "narrator",
            "characters": [
                {
                    "canonical": "Narrator",
                    "aliases": ["the narrator"],
                    "description": "The narrator.",
                    "gender": "unknown",
                    "age": "adult",
                    "persona_of": None,
                    "voice_id": "narrator",
                    "first_evidence_quote": "Once upon a time.",
                },
                {
                    "canonical": "Alice",
                    "aliases": ["Alice"],
                    "description": "A curious child speaking to herself.",
                    "gender": "female",
                    "age": "child",
                    "persona_of": None,
                    "voice_id": "alice",
                    "first_evidence_quote": "what is the use of a book",
                },
            ],
        }])

        registry = build_character_registry(
            BookContext(
                title="Alice",
                author="Lewis Carroll",
                language="en",
                opening_text='"what is the use of a book," thought Alice',
            ),
            llm,
            prompt_cfg=type("Cfg", (), {
                "voice_pool_description": "Voices: narrator, alice",
            })(),
            voice_pool=voices,
        )

        self.assertNotIn("Narrator", registry.characters)
        self.assertIn("Alice", registry.characters)

    def test_retries_empty_registry_for_dialogue_sample(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="alice", preset_name="bf_alice"),
        ]
        llm = StubLLM([
            {"narrator_voice_id": "narrator", "characters": []},
            {
                "narrator_voice_id": "narrator",
                "characters": [{
                    "canonical": "Alice",
                    "aliases": ["Alice"],
                    "description": "A curious child speaking to herself.",
                    "gender": "female",
                    "age": "child",
                    "persona_of": None,
                    "voice_id": "alice",
                    "first_evidence_quote": "what is the use of a book",
                }],
            },
        ])

        registry = build_character_registry(
            BookContext(
                title="Alice's Adventures in Wonderland",
                author="Lewis Carroll",
                language="en",
                opening_text='"what is the use of a book," thought Alice',
            ),
            llm,
            prompt_cfg=type("Cfg", (), {
                "voice_pool_description": "Voices: narrator, alice",
            })(),
            voice_pool=voices,
        )

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Alice", registry.characters)
        self.assertEqual(registry.characters["Alice"].voice.id, "alice")

    def test_empty_registry_for_dialogue_sample_fails_after_retry(self):
        voices = [VoiceSpec(id="narrator", preset_name="af_heart")]
        llm = StubLLM([
            {"narrator_voice_id": "narrator", "characters": []},
            {"narrator_voice_id": "narrator", "characters": []},
        ])

        with self.assertRaises(ValueError):
            build_character_registry(
                BookContext(
                    title="Alice",
                    author="Lewis Carroll",
                    language="en",
                    opening_text='"Oh dear!" said the Rabbit.',
                ),
                llm,
                prompt_cfg=type("Cfg", (), {
                    "voice_pool_description": "Voices: narrator",
                })(),
                voice_pool=voices,
            )

    def test_registry_prompt_guides_childrens_narrator_selection(self):
        voices = [
            VoiceSpec(id="af_heart", preset_name="af_heart"),
            VoiceSpec(id="bm_george", preset_name="bm_george"),
        ]
        llm = StubLLM([{
            "narrator_voice_id": "af_heart",
            "characters": [{
                "canonical": "Alice",
                "aliases": ["Alice"],
                "description": "A curious child speaking to herself.",
                "gender": "female",
                "age": "child",
                "persona_of": None,
                "voice_id": "af_heart",
                "first_evidence_quote": "what is the use of a book",
            }],
        }])

        build_character_registry(
            BookContext(
                title="Alice's Adventures in Wonderland",
                author="Lewis Carroll",
                language="en",
                opening_text='"what is the use of a book," thought Alice',
            ),
            llm,
            prompt_cfg=type("Cfg", (), {
                "voice_pool_description": "af_heart warm narrator; bm_george authoritative deep British",
            })(),
            voice_pool=voices,
        )

        self.assertIn("whimsical children's fiction", llm.calls[0]["system"])
        self.assertIn("Do not default to", llm.calls[0]["system"])


class TestChapterAttribution(unittest.TestCase):
    def test_chapter_level_attribution_adds_new_character_and_tiles_chunks(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="holmes", preset_name="bm_george"),
            VoiceSpec(id="watson", preset_name="am_eric"),
        ]
        registry = CharacterRegistry(
            narrator_voice=voices[0],
            characters={
                "Sherlock Holmes": CharacterProfile(
                    canonical="Sherlock Holmes",
                    aliases=["Holmes"],
                    description="A detective.",
                    voice=voices[1],
                ),
            },
        )
        chapter = ChapterWindow(
            title="A Study",
            chunks=[
                ChunkWindow("", 'Holmes said, "Come."', 'Watson replied, "At once."'),
                ChunkWindow('Holmes said, "Come."', 'Watson replied, "At once."', ""),
            ],
        )
        llm = StubLLM([{
            "new_characters": [{
                "canonical": "Dr. Watson",
                "aliases": ["Watson"],
                "description": "A warm doctor.",
                "gender": "male",
                "age": "adult",
                "persona_of": None,
                "voice_id": "watson",
                "first_evidence_quote": '"At once."',
            }],
            "quote_speakers": [
                {"quote_id": 0, "speaker": "Holmes"},
                {"quote_id": 1, "speaker": "Dr. Watson"},
            ],
        }])

        attribution = annotate_chapter_speakers(
            chapter,
            registry,
            llm,
            AnnotationPromptConfig(speaker_rules=""),
            voices,
        )

        self.assertIn("Dr. Watson", attribution.registry.characters)
        self.assertEqual(
            [
                [span.speaker for span in chunk_spans]
                for chunk_spans in attribution.chunk_spans
            ],
            [
                ["narrator", "Sherlock Holmes"],
                ["narrator", "Dr. Watson"],
            ],
        )
        for window, spans in zip(chapter.chunks, attribution.chunk_spans):
            self.assertEqual("".join(window.text[s.start:s.end] for s in spans), window.text)

    def test_chapter_attribution_repairs_source_grounded_typo(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="dormouse", preset_name="bf_alice"),
        ]
        registry = CharacterRegistry(narrator_voice=voices[0], characters={})
        text = (
            '"Treacle," said a sleepy voice behind her.\n\n'
            '"Collar that Dormouse," the Queen shrieked out.'
        )
        chapter = ChapterWindow(title="XI", chunks=[ChunkWindow("", text, "")])
        llm = StubLLM([{
            "new_characters": [{
                "canonical": "Dormmouse",
                "aliases": ["Dormmouse"],
                "description": "A sleepy creature.",
                "gender": "unknown",
                "age": "unknown",
                "persona_of": None,
                "voice_id": "dormouse",
                "first_evidence_quote": '"Treacle,"',
            }],
            "quote_speakers": [
                {"quote_id": 0, "speaker": "Dormmouse"},
                {"quote_id": 1, "speaker": "narrator"},
            ],
        }])

        attribution = annotate_chapter_speakers(
            chapter,
            registry,
            llm,
            AnnotationPromptConfig(speaker_rules=""),
            voices,
        )

        self.assertIn("Dormouse", attribution.registry.characters)
        self.assertNotIn("Dormmouse", attribution.registry.characters)
        self.assertTrue(any(
            span.speaker == "Dormouse"
            for span in attribution.chunk_spans[0]
        ))

    def test_chapter_attribution_can_create_minimal_character_from_safe_repair(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="character", preset_name="bf_alice"),
        ]
        registry = CharacterRegistry(narrator_voice=voices[0], characters={})
        text = '"Treacle," said the Dormouse.'
        chapter = ChapterWindow(title="XI", chunks=[ChunkWindow("", text, "")])
        llm = StubLLM([{
            "new_characters": [],
            "quote_speakers": [
                {"quote_id": 0, "speaker": "Dormmouse"},
            ],
        }])

        attribution = annotate_chapter_speakers(
            chapter,
            registry,
            llm,
            AnnotationPromptConfig(speaker_rules=""),
            voices,
        )

        self.assertIn("Dormouse", attribution.registry.characters)
        self.assertEqual(attribution.chunk_spans[0][0].speaker, "Dormouse")

    def test_short_ambiguous_name_uses_identity_adjudicator_not_auto_repair(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="jamie", preset_name="am_liam"),
        ]
        registry = CharacterRegistry(
            narrator_voice=voices[0],
            characters={
                "Jamie": CharacterProfile(
                    canonical="Jamie",
                    aliases=["Jamie"],
                    description="A young speaker.",
                    voice=voices[1],
                )
            },
        )
        text = '"Coming," said Jamie.'
        chapter = ChapterWindow(title="Ambiguous", chunks=[ChunkWindow("", text, "")])
        llm = StubLLM([
            {
                "new_characters": [],
                "quote_speakers": [
                    {"quote_id": 0, "speaker": "James"},
                ],
            },
            {"decision": "unresolved", "canonical": ""},
        ])

        with self.assertRaises(ValueError):
            annotate_chapter_speakers(
                chapter,
                registry,
                llm,
                AnnotationPromptConfig(speaker_rules=""),
                voices,
            )

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Proposed speaker: James", llm.calls[1]["user"])

    def test_merge_new_characters_skips_existing_aliases_and_narrator(self):
        registry = _registry()
        voices = [
            registry.narrator_voice,
            VoiceSpec(id="moriarty", preset_name="am_adam"),
        ]

        merged = merge_new_characters(registry, [
            {
                "canonical": "Narrator",
                "aliases": ["the narrator"],
                "description": "Narration.",
                "gender": "unknown",
                "age": "adult",
                "persona_of": None,
                "voice_id": "narrator",
                "first_evidence_quote": "Once.",
            },
            {
                "canonical": "Mr. Holmes",
                "aliases": ["Holmes"],
                "description": "Duplicate.",
                "gender": "male",
                "age": "adult",
                "persona_of": None,
                "voice_id": "moriarty",
                "first_evidence_quote": '"Come."',
            },
            {
                "canonical": "Professor Moriarty",
                "aliases": ["Moriarty"],
                "description": "A criminal mastermind.",
                "gender": "male",
                "age": "adult",
                "persona_of": None,
                "voice_id": "moriarty",
                "first_evidence_quote": '"Indeed."',
            },
        ], voices)

        self.assertNotIn("Narrator", merged.characters)
        self.assertNotIn("Mr. Holmes", merged.characters)
        self.assertIn("Professor Moriarty", merged.characters)

    def test_chapter_attribution_batches_large_quote_sets(self):
        voices = [
            VoiceSpec(id="narrator", preset_name="af_heart"),
            VoiceSpec(id="alice", preset_name="bf_alice"),
        ]
        registry = CharacterRegistry(narrator_voice=voices[0], characters={})
        text = " ".join(f'Alice said "line {i}".' for i in range(21))
        chapter = ChapterWindow(title="Many Quotes", chunks=[ChunkWindow("", text, "")])
        llm = StubLLM([
            {
                "new_characters": [{
                    "canonical": "Alice",
                    "aliases": ["Alice"],
                    "description": "A curious child.",
                    "gender": "female",
                    "age": "child",
                    "persona_of": None,
                    "voice_id": "alice",
                    "first_evidence_quote": '"line 0"',
                }],
                "quote_speakers": [
                    {"quote_id": i, "speaker": "Alice"}
                    for i in range(20)
                ],
            },
            {
                "new_characters": [],
                "quote_speakers": [
                    {"quote_id": 20, "speaker": "Alice"},
                ],
            },
        ])

        attribution = annotate_chapter_speakers(
            chapter,
            registry,
            llm,
            AnnotationPromptConfig(speaker_rules=""),
            voices,
        )

        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Alice", attribution.registry.characters)
        self.assertTrue(
            any(span.speaker == "Alice" for span in attribution.chunk_spans[0])
        )

    def test_chapter_prompt_defaults_embedded_dialogue_to_reciter(self):
        voices = [VoiceSpec(id="narrator", preset_name="af_heart")]
        registry = CharacterRegistry(narrator_voice=voices[0], characters={})
        chapter = ChapterWindow(title="Poem", chunks=[ChunkWindow("", '"Line."', "")])
        llm = StubLLM([{
            "new_characters": [],
            "quote_speakers": [{"quote_id": 0, "speaker": "narrator"}],
        }])

        with patch("openshelf.config.EMBEDDED_DIALOGUE_MODE", "reciter"):
            annotate_chapter_speakers(
                chapter,
                registry,
                llm,
                AnnotationPromptConfig(speaker_rules=""),
                voices,
            )

        self.assertIn("assign those embedded quoted lines to the reciter", llm.calls[0]["system"])


class TestAssignVoices(unittest.TestCase):
    def test_deterministic_gender_hint_and_wrap(self):
        pool = [
            VoiceSpec(id="af_heart", preset_name="af_heart"),
            VoiceSpec(id="am_eric", preset_name="am_eric"),
        ]
        characters = [
            {"canonical": "Alice", "gender": "female"},
            {"canonical": "Bob", "gender": "male"},
            {"canonical": "Carol", "gender": "female"},
        ]
        assigned = assign_voices(characters, pool)
        self.assertEqual(assigned["Alice"].id, "af_heart")
        self.assertEqual(assigned["Bob"].id, "am_eric")
        self.assertEqual(assigned["Carol"].id, "af_heart")

    def test_voice_id_hint_used(self):
        pool = [
            VoiceSpec(id="af_heart", preset_name="af_heart"),
            VoiceSpec(id="bm_george", preset_name="bm_george"),
        ]
        assigned = assign_voices([
            {"canonical": "Holmes", "gender": "male", "voice_id": "bm_george"},
        ], pool)
        self.assertEqual(assigned["Holmes"].id, "bm_george")


class TestAnnotateWithFallback(unittest.TestCase):
    def test_no_quotes_skips_llm(self):
        llm = StubLLM([])
        cfg = AnnotationPromptConfig(speaker_rules="")
        segments = annotate_with_fallback(
            ChunkWindow(prev="", text="Plain narration.", next=""),
            _registry(),
            llm,
            cfg,
        )
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "narrator")
        self.assertEqual(len(llm.calls), 0)

    def test_good_spans_return_multi_segment(self):
        text = 'He said, "Come."'
        llm = StubLLM([{"quote_speakers": [
            {"quote_id": 0, "speaker": "Holmes"},
        ]}])
        cfg = AnnotationPromptConfig(speaker_rules="")
        segments = annotate_with_fallback(ChunkWindow("", text, ""), _registry(), llm, cfg)
        self.assertEqual([s.speaker for s in segments], ["narrator", "Sherlock Holmes"])
        self.assertEqual("".join(s.text for s in segments), text)

    def test_missing_quote_assignment_fallback_to_narrator(self):
        text = '"Come," he said.'
        llm = StubLLM([{"quote_speakers": []}])
        cfg = AnnotationPromptConfig(speaker_rules="")
        segments = annotate_with_fallback(ChunkWindow("", text, ""), _registry(), llm, cfg)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "narrator")
        self.assertEqual(segments[0].text, text)

    def test_unknown_speaker_fallback_to_narrator(self):
        text = '"Come," said the stranger.'
        llm = StubLLM([{"quote_speakers": [
            {"quote_id": 0, "speaker": "Moriarty"},
        ]}])
        cfg = AnnotationPromptConfig(speaker_rules="")
        segments = annotate_with_fallback(ChunkWindow("", text, ""), _registry(), llm, cfg)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "narrator")
        self.assertEqual(segments[0].text, text)

    def test_llm_error_fallback_to_narrator(self):
        text = '"Come," he said.'
        llm = StubLLM([LLMError("bad call")])
        cfg = AnnotationPromptConfig(speaker_rules="")
        segments = annotate_with_fallback(ChunkWindow("", text, ""), _registry(), llm, cfg)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].speaker, "narrator")

    def test_sync_invariant(self):
        text = 'He said, "Come."'
        llm = StubLLM([{"quote_speakers": [
            {"quote_id": 0, "speaker": "Holmes"},
        ]}])
        cfg = AnnotationPromptConfig(speaker_rules="")
        segments = annotate_with_fallback(ChunkWindow("", text, ""), _registry(), llm, cfg)
        self.assertEqual("".join(s.text for s in segments), text)


class TestVoiceDirectionArtifact(unittest.TestCase):
    def test_directed_chunks_round_trip_through_artifact(self):
        import json
        import tempfile

        chapter = build_direction_chapter(
            3,
            "Chapter",
            ["Narrator. Quote."],
            [[
                DirectedSegment(
                    text="Directed narrator.",
                    voice=VoiceSpec(id="narrator", preset_name="af_heart"),
                    speaker="narrator",
                    emotion="gentle",
                    speed=0.95,
                    pause_after_ms=120,
                    original_text="Narrator.",
                    delivery_type="narration",
                    voice_policy="narrator",
                    join_policy="tight",
                    engine_controls={"style": "soft"},
                ),
            ]],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chapter-03.voice_direction.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    build_voice_direction_payload(
                        "kokoro-af-heart", "abc123", "kokoro", "solo", [chapter]
                    ),
                    f,
                )
            directed_chunks = directed_chunks_from_chapter_direction_artifact(path)

        self.assertEqual(len(directed_chunks), 1)
        segment = directed_chunks[0][0]
        self.assertEqual(segment.text, "Directed narrator.")
        self.assertEqual(segment.voice.preset_name, "af_heart")
        self.assertEqual(segment.emotion, "gentle")
        self.assertEqual(segment.speed, 0.95)
        self.assertEqual(segment.pause_after_ms, 120)
        self.assertEqual(segment.original_text, "Narrator.")
        self.assertEqual(segment.join_policy, "tight")
        self.assertEqual(segment.engine_controls, {"style": "soft"})

    def test_directed_segment_to_dict_keeps_original_and_synthesis_text(self):
        segment = DirectedSegment(
            text="Synth.",
            voice=VoiceSpec(id="narrator"),
            speaker="narrator",
            original_text="Original.",
        )
        payload = directed_segment_to_dict(segment)
        self.assertEqual(payload["original_text"], "Original.")
        self.assertEqual(payload["synthesis_text"], "Synth.")

    def test_directed_chunks_artifact_rejects_multi_chapter(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.voice_direction.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"chapters": [{"chunks": []}, {"chunks": []}]}, f)
            with self.assertRaises(ValueError):
                directed_chunks_from_chapter_direction_artifact(path)


if __name__ == "__main__":
    unittest.main()
