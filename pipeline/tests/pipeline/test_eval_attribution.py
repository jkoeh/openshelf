"""Offline attribution eval harness for golden dialogue snippets."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.llm import ReplayLLM  # noqa: E402
from openshelf.pipeline.tts_engine import AnnotationPromptConfig, VoiceSpec  # noqa: E402
from openshelf.pipeline.voice_director import (  # noqa: E402
    CharacterProfile,
    CharacterRegistry,
    ChunkWindow,
    annotate_with_fallback,
    extract_quote_spans,
)


FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "fixtures",
    "dialogue_golden.json",
)


class FixtureWritingLLM:
    name = "fixture-writer"

    def __init__(self, fixtures_dir: str, response: dict):
        self.fixtures_dir = fixtures_dir
        self.response = response

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        key = ReplayLLM.fixture_key(system, user)
        path = os.path.join(self.fixtures_dir, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.response, f, indent=2, sort_keys=True)
        return self.response


def _registry() -> CharacterRegistry:
    return CharacterRegistry(
        narrator_voice=VoiceSpec(id="narrator", preset_name="af_heart"),
        characters={
            "Sherlock Holmes": CharacterProfile(
                canonical="Sherlock Holmes",
                aliases=["Holmes", "the detective"],
                description="A precise detective.",
                voice=VoiceSpec(id="holmes", preset_name="bm_george"),
            ),
        },
    )


class TestAttributionEval(unittest.TestCase):
    def test_golden_cases_with_replay_fixtures(self):
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            cases = json.load(f)

        cfg = AnnotationPromptConfig(speaker_rules="")
        registry = _registry()
        with tempfile.TemporaryDirectory() as tmp:
            for case in cases:
                quote_speakers = []
                for quote in extract_quote_spans(case["text"]):
                    speaker = "narrator"
                    for span in case["expected_spans"]:
                        if span["start"] <= quote.start and span["end"] >= quote.end:
                            speaker = span["speaker"]
                            break
                    quote_speakers.append({
                        "quote_id": quote.quote_id,
                        "speaker": speaker,
                    })
                response = {"quote_speakers": quote_speakers}
                writer = FixtureWritingLLM(tmp, response)
                window = ChunkWindow("", case["text"], "")

                # First pass creates the replay fixture for quote-bearing cases.
                annotate_with_fallback(window, registry, writer, cfg)

                # Second pass proves the same path is replayable offline.
                segments = annotate_with_fallback(window, registry, ReplayLLM(tmp), cfg)
                self.assertEqual(
                    "".join(segment.text for segment in segments),
                    case["text"],
                    case["id"],
                )
                self.assertEqual(
                    [segment.speaker for segment in segments],
                    [
                        span["speaker"]
                        for span in case["expected_spans"]
                        if span["end"] > span["start"]
                    ],
                    case["id"],
                )


class TestOnlineAttributionEval(unittest.TestCase):
    @unittest.skipUnless(os.getenv("RUN_LLM_EVALS"), "set RUN_LLM_EVALS=1")
    def test_online_eval_placeholder(self):
        self.skipTest("Online eval fixture recording is opt-in and not run in CI")


if __name__ == "__main__":
    unittest.main()
