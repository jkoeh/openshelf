"""Tests for build-catalog.py against the build-versioned manifest layout."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "build-catalog.py",
)
spec = importlib.util.spec_from_file_location("build_catalog_script", SCRIPT_PATH)
build_catalog_script = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["build_catalog_script"] = build_catalog_script
spec.loader.exec_module(build_catalog_script)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "HeadObject")


class FakeClient:
    def __init__(self, objects: dict[str, str], existing_keys: set[str] | None = None):
        self.objects = objects
        self.existing_keys = existing_keys or set(objects.keys())

    def get_paginator(self, _name: str):
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": key} for key in sorted(self.objects.keys())]},
        ]
        return paginator

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3-style args
        if Key not in self.objects:
            raise _client_error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key].encode("utf-8"))}

    def head_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3-style args
        if Key not in self.existing_keys:
            raise _client_error("404")
        return {}


class TestBuildCatalog(unittest.TestCase):
    def test_parse_manifest_key_accepts_only_root_book_manifest(self):
        self.assertEqual(
            build_catalog_script.parse_manifest_key("books/franz-kafka/the-trial/manifest.json"),
            {"author_slug": "franz-kafka", "title_slug": "the-trial"},
        )
        self.assertIsNone(
            build_catalog_script.parse_manifest_key(
                "books/franz-kafka/the-trial/audio/kokoro-af-heart/manifest.json",
            ),
        )

    def test_build_catalog_uses_default_rendition_current_build(self):
        objects = {
            "books/franz-kafka/the-trial/manifest.json": """
                {
                  "title": "The Trial",
                  "author": "Franz Kafka",
                  "source": "gutenberg",
                  "renditions": {
                    "kokoro-bf-emma": {
                      "voice": "bf_emma",
                      "engine": "kokoro",
                      "display": "Emma",
                      "current_build": "aaaaaaaaaaaaaaaa",
                      "available_builds": ["aaaaaaaaaaaaaaaa"]
                    },
                    "kokoro-af-heart": {
                      "voice": "af_heart",
                      "engine": "kokoro",
                      "display": "Heart",
                      "current_build": "2a4f9c1b3d8e7f60",
                      "available_builds": ["2a4f9c1b3d8e7f60"]
                    }
                  }
                }
            """,
            "books/franz-kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1b3d8e7f60/rendition-manifest.json": """
                {
                  "build": "2a4f9c1b3d8e7f60",
                  "rendition": "kokoro-af-heart",
                  "voice": "af_heart",
                  "engine": "kokoro",
                  "pipeline_version": "1",
                  "total_duration_seconds": 1847.3,
                  "chapters": [
                    {"number": 1, "title": "I", "filename": "chapter-01.m4a",
                     "duration_seconds": 1847.3, "word_count": 3241}
                  ]
                }
            """,
        }
        existing = set(objects.keys()) | {"books/franz-kafka/the-trial/cover.jpg"}
        client = FakeClient(objects, existing_keys=existing)

        with patch("build_catalog_script.datetime") as dt:
            dt.now.return_value.isoformat.return_value = "2026-01-01T00:00:00+00:00"
            with redirect_stdout(io.StringIO()):
                catalog = build_catalog_script.build_catalog(client, "openshelf")

        self.assertEqual(catalog["generated_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(len(catalog["books"]), 1)
        book = catalog["books"][0]
        self.assertEqual(book["rendition"], "kokoro-af-heart")
        self.assertEqual(book["total_duration_seconds"], 1847.3)
        self.assertEqual(book["chapter_count"], 1)
        self.assertTrue(book["has_cover"])

    def test_choose_catalog_rendition_falls_back_to_first_sorted_key(self):
        self.assertEqual(
            build_catalog_script.choose_catalog_rendition({
                "kokoro-bf-emma": {},
                "kokoro-af-nicole": {},
            }),
            "kokoro-af-nicole",
        )


if __name__ == "__main__":
    unittest.main()
