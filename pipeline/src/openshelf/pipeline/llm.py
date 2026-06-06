"""LLM clients for voice casting and direction.

Production clients are lazy so importing this module never requires network
credentials or third-party SDKs in offline tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from openshelf.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_MAX_OUTPUT_TOKENS,
    OPENAI_MODEL,
)


class LLMError(Exception):
    """Base error for LLM client failures."""


class StubExhausted(LLMError):
    """Raised when StubLLM has no canned responses left."""


class FixtureMissing(LLMError):
    """Raised when ReplayLLM has no fixture for a prompt."""


@runtime_checkable
class LLMClient(Protocol):
    name: str

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        """Return parsed JSON matching the supplied schema."""


class StubLLM:
    name = "stub"

    def __init__(self, responses: list[dict | Exception]):
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self._responses:
            raise StubExhausted("StubLLM has no responses left")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ReplayLLM:
    name = "replay"

    def __init__(self, fixtures_dir: str | Path):
        self.fixtures_dir = Path(fixtures_dir)

    @staticmethod
    def fixture_key(system: str, user: str) -> str:
        return hashlib.sha256((system + user).encode("utf-8")).hexdigest()[:16]

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        key = self.fixture_key(system, user)
        path = self.fixtures_dir / f"{key}.json"
        if not path.exists():
            raise FixtureMissing(f"Missing replay fixture: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


class RecordingLLM:
    name = "recording"

    def __init__(self, wrapped: LLMClient, fixtures_dir: str | Path):
        self.wrapped = wrapped
        self.fixtures_dir = Path(fixtures_dir)

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        response = self.wrapped.complete_json(system=system, user=user, schema=schema)
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        key = ReplayLLM.fixture_key(system, user)
        path = self.fixtures_dir / f"{key}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2, ensure_ascii=False, sort_keys=True)
        return response


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or ANTHROPIC_MODEL
        self.api_key = api_key or ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("anthropic package is not installed") from e

        client = anthropic.Anthropic(api_key=self.api_key)
        message = client.messages.create(
            model=self.model,
            temperature=0,
            max_tokens=4096,
            system=system,
            messages=[{
                "role": "user",
                "content": (
                    f"{user}\n\nReturn JSON matching this schema only:\n"
                    f"{json.dumps(schema, sort_keys=True)}"
                ),
            }],
        )
        text_parts = [
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        ]
        try:
            return json.loads("".join(text_parts))
        except json.JSONDecodeError as e:
            raise LLMError("Anthropic response was not valid JSON") from e


class OpenAILLM:
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_output_tokens: int | None = None,
    ):
        self.model = model or OPENAI_MODEL
        self.api_key = api_key or OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
        self.max_output_tokens = max_output_tokens or OPENAI_MAX_OUTPUT_TOKENS

    def complete_json(self, *, system: str, user: str, schema: dict) -> dict:
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("openai package is not installed") from e

        client = OpenAI(api_key=self.api_key)
        strict = bool(schema.get("x-openai-strict"))
        api_schema = _strip_schema_metadata(schema)
        response = client.responses.create(
            model=self.model,
            max_output_tokens=self.max_output_tokens,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "openshelf_voice_direction",
                    "schema": api_schema,
                    "strict": strict,
                },
            },
        )
        text = getattr(response, "output_text", None)
        if not text:
            text = _response_text(response)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("OpenAI response was not valid JSON") from e


def _response_text(response: object) -> str:
    """Extract text from a Responses API object without depending on SDK internals."""
    parts: list[str] = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(text)
    return "".join(parts)


def _strip_schema_metadata(schema: dict) -> dict:
    """Remove local schema metadata before sending JSON Schema to providers."""
    def strip(value):
        if isinstance(value, dict):
            return {
                key: strip(item)
                for key, item in value.items()
                if not key.startswith("x-")
            }
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    return strip(schema)


def create_llm(provider: str | None = None) -> LLMClient:
    selected = (provider or LLM_PROVIDER or os.getenv("LLM_PROVIDER", "anthropic")).lower()
    if selected == "anthropic":
        return AnthropicLLM()
    if selected == "openai":
        return OpenAILLM()
    if selected == "replay":
        fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "llm"
        return ReplayLLM(fixtures)
    raise LLMError(f"Unsupported LLM provider: {selected}")
