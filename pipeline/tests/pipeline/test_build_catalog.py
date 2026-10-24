"""Tests for catalog building against the build-versioned manifest layout."""

from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.ops import catalog


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
    def test_upload_syncs_publication_keys_in_bounded_batches_and_requires_api_success(self):
        books = [{"author_slug": "owner", "title_slug": f"book-{index}"} for index in range(101)]
        built = {"version": 2, "generated_at": "now", "books": books}
        client = MagicMock()
        with patch.dict(os.environ, {"OPENSHELF_API_BASE": "https://worker.example/api/v1", "OPENSHELF_PC_TOKEN": "pc-token-longer-than-twenty-four"}), \
                patch.object(catalog, "build_catalog", return_value=built), \
                patch("openshelf.pipeline.job_consumer.JobAPI") as api_class, redirect_stdout(io.StringIO()):
            api = api_class.return_value
            api.post.side_effect = lambda path, body: {"synced": len(body["books"])} if path.endswith("/sync") else {"checked": 0, "removed": 0, "after": None}
            self.assertEqual(catalog.build_and_upload_catalog(client=client), built)
            api_class.assert_called_once_with("https://worker.example/api/v1", "pc-token-longer-than-twenty-four")
            self.assertEqual([len(call.args[1]["books"]) for call in api.post.call_args_list if call.args[0].endswith("/sync")], [10] * 10 + [1])
            self.assertEqual(api.post.call_args_list[-1].args, ("/internal/catalog/reconcile", {}))
            client.put_object.assert_called_once()
            api.post.side_effect = RuntimeError("sync failed")
            with self.assertRaisesRegex(RuntimeError, "sync failed"):
                catalog.build_and_upload_catalog(client=client)
            api.post.side_effect = None
            api.post.return_value = {"synced": 0}
            with self.assertRaisesRegex(RuntimeError, "every publication"):
                catalog.build_and_upload_catalog(client=client)

    def test_non_dry_publication_requires_explicit_api_environment_before_writing(self):
        client = MagicMock()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENSHELF_API_BASE"):
                catalog.build_and_upload_catalog(client=client)
        client.put_object.assert_not_called()

    def test_empty_r2_catalog_still_reconciles_all_existing_d1_rows(self):
        built = {"version": 2, "generated_at": "now", "books": []}
        cursor = {"author_slug": "owner", "title_slug": "last-book"}
        with patch.dict(os.environ, {"OPENSHELF_API_BASE": "https://worker.example/api/v1", "OPENSHELF_PC_TOKEN": "pc-token-longer-than-twenty-four"}), \
                patch.object(catalog, "build_catalog", return_value=built), \
                patch("openshelf.pipeline.job_consumer.JobAPI") as api_class, redirect_stdout(io.StringIO()):
            api = api_class.return_value
            api.post.side_effect = [{"checked": 10, "removed": 10, "after": cursor}, {"checked": 1, "removed": 1, "after": None}]
            catalog.build_and_upload_catalog(client=MagicMock())
            self.assertEqual([call.args for call in api.post.call_args_list], [
                ("/internal/catalog/reconcile", {}), ("/internal/catalog/reconcile", {"after": cursor})])

    def test_dry_catalog_never_creates_an_api_client(self):
        built = {"version": 2, "generated_at": "now", "books": []}
        client = MagicMock()
        with patch.object(catalog, "build_catalog", return_value=built), \
                patch("openshelf.pipeline.job_consumer.JobAPI") as api, redirect_stdout(io.StringIO()):
            self.assertEqual(catalog.build_and_upload_catalog(dry_run=True, client=client), built)
        api.assert_not_called()
        client.put_object.assert_not_called()

    def test_parse_manifest_key_accepts_only_root_book_manifest(self):
        self.assertEqual(
            catalog.parse_manifest_key("books/franz-kafka/the-trial/manifest.json"),
            {"author_slug": "franz-kafka", "title_slug": "the-trial"},
        )
        self.assertIsNone(
            catalog.parse_manifest_key(
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
                  "version": 2,
                  "build": "2a4f9c1b3d8e7f60",
                  "rendition": "kokoro-af-heart",
                  "voice": "af_heart",
                  "engine": "kokoro",
                  "pipeline_version": "2",
                  "total_duration_seconds": 1847.3,
                  "section_count": 1,
                  "sections": [
                    {"sequence": 1, "section_type": "chapter", "ordinal": 1,
                     "display_label": "I", "display_title": "The Arrest",
                     "filename": "section-01.m4a",
                     "duration_seconds": 1847.3, "word_count": 3241}
                  ]
                }
            """,
        }
        existing = set(objects.keys()) | {"books/franz-kafka/the-trial/cover.jpg"}
        client = FakeClient(objects, existing_keys=existing)

        with patch("openshelf.pipeline.ops.catalog.datetime") as dt:
            dt.now.return_value.isoformat.return_value = "2026-01-01T00:00:00+00:00"
            with redirect_stdout(io.StringIO()):
                built = catalog.build_catalog(client, "openshelf")

        self.assertEqual(built["generated_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(len(built["books"]), 1)
        book = built["books"][0]
        self.assertEqual(book["rendition"], "kokoro-af-heart")
        self.assertEqual(book["current_build"], "2a4f9c1b3d8e7f60")
        self.assertEqual(book["total_duration_seconds"], 1847.3)
        self.assertEqual(book["section_count"], 1)
        self.assertTrue(book["has_cover"])

    def test_choose_catalog_rendition_falls_back_to_first_sorted_key(self):
        self.assertEqual(
            catalog.choose_catalog_rendition({
                "kokoro-bf-emma": {},
                "kokoro-af-nicole": {},
            }),
            "kokoro-af-nicole",
        )


if __name__ == "__main__":
    unittest.main()
