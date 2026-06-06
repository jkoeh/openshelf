"""Tests for LLM and TTS protocol contracts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.llm import (  # noqa: E402
    FixtureMissing,
    LLMClient,
    LLMError,
    OpenAILLM,
    ReplayLLM,
    StubExhausted,
    StubLLM,
    create_llm,
)
from openshelf.pipeline.tts_engine import NullAligner, WordAligner  # noqa: E402


class TestStubLLM(unittest.TestCase):
    def test_returns_responses_in_sequence(self):
        llm = StubLLM([{"a": 1}, {"b": 2}])

        self.assertEqual(llm.complete_json(system="s", user="u", schema={}), {"a": 1})
        self.assertEqual(llm.complete_json(system="s", user="u", schema={}), {"b": 2})
        self.assertEqual(len(llm.calls), 2)

    def test_raises_when_exhausted(self):
        llm = StubLLM([])

        with self.assertRaises(StubExhausted):
            llm.complete_json(system="s", user="u", schema={})

    def test_raises_canned_exception(self):
        llm = StubLLM([LLMError("boom")])

        with self.assertRaises(LLMError):
            llm.complete_json(system="s", user="u", schema={})

    def test_protocol_conformance(self):
        self.assertIsInstance(StubLLM([]), LLMClient)


class TestReplayLLM(unittest.TestCase):
    def test_returns_fixture_for_known_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = ReplayLLM.fixture_key("system", "user")
            path = os.path.join(tmp, f"{key}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ok": True}, f)

            llm = ReplayLLM(tmp)
            self.assertEqual(
                llm.complete_json(system="system", user="user", schema={}),
                {"ok": True},
            )

    def test_raises_for_missing_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            llm = ReplayLLM(tmp)
            with self.assertRaises(FixtureMissing):
                llm.complete_json(system="missing", user="fixture", schema={})


class TestOpenAILLM(unittest.TestCase):
    def test_calls_responses_api_with_json_schema(self):
        calls = []

        class FakeResponses:
            def create(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(output_text='{"ok": true}')

        class FakeClient:
            def __init__(self, api_key):
                self.api_key = api_key
                self.responses = FakeResponses()

        fake_module = types.SimpleNamespace(OpenAI=FakeClient)

        with patch.dict(sys.modules, {"openai": fake_module}):
            llm = OpenAILLM(
                model="gpt-test",
                api_key="test-key",
                max_output_tokens=123,
            )
            result = llm.complete_json(
                system="system prompt",
                user="user prompt",
                schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls[0]["model"], "gpt-test")
        self.assertEqual(calls[0]["max_output_tokens"], 123)
        self.assertEqual(calls[0]["input"][0]["role"], "system")
        self.assertEqual(calls[0]["input"][1]["role"], "user")
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")
        self.assertEqual(calls[0]["text"]["format"]["name"], "openshelf_voice_direction")

    def test_falls_back_to_output_content_text(self):
        class FakeResponses:
            def create(self, **kwargs):
                content = [types.SimpleNamespace(text='{"fallback": true}')]
                output = [types.SimpleNamespace(content=content)]
                return types.SimpleNamespace(output_text="", output=output)

        class FakeClient:
            def __init__(self, api_key):
                self.responses = FakeResponses()

        fake_module = types.SimpleNamespace(OpenAI=FakeClient)

        with patch.dict(sys.modules, {"openai": fake_module}):
            result = OpenAILLM(api_key="test-key").complete_json(
                system="s",
                user="u",
                schema={"type": "object"},
            )

        self.assertEqual(result, {"fallback": True})

    def test_raises_when_api_key_missing(self):
        with patch("openshelf.pipeline.llm.OPENAI_API_KEY", ""), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMError):
                OpenAILLM(api_key="").complete_json(system="s", user="u", schema={})

    def test_create_llm_supports_openai_provider(self):
        self.assertIsInstance(create_llm("openai"), OpenAILLM)


class TestAlignerProtocol(unittest.TestCase):
    def test_null_aligner_protocol_conformance(self):
        aligner = NullAligner()
        self.assertIsInstance(aligner, WordAligner)
        self.assertEqual(aligner.align(np.zeros(10, dtype=np.float32), "hello", 24000), [])


if __name__ == "__main__":
    unittest.main()
