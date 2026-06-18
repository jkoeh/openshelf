"""Tests for r2.py — the build-versioned R2 uploader.

See pipeline/docs/step6-r2.md for the layout these uploaders write into.
All key construction is routed through r2_keys; this file tests the
upload behaviour (HTTP method choice via boto3, headers, idempotency gate,
ordering, force semantics) and trusts r2_keys' own tests for path
correctness.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.config import R2_CACHE_CONTROL_IMMUTABLE, R2_CACHE_CONTROL_MANIFEST
from openshelf.pipeline.r2 import (
    key_exists,
    make_client,
    upload_book_manifest,
    upload_cover,
    upload_epub,
    upload_rendition_build,
)


BUCKET = "openshelf"
AUTHOR = "kafka"
TITLE = "the-trial"
RENDITION = "kokoro-af-heart"
BUILD = "2a4f9c1"


def _make_build_dir(tmp_dir: str, n_chapters: int = 2) -> tuple[str, str, str]:
    """Lay out a fake build directory: audio_dir, chapter_data.json, rendition-manifest.json."""
    audio_dir = os.path.join(tmp_dir, "audio")
    os.makedirs(audio_dir)
    for i in range(1, n_chapters + 1):
        open(os.path.join(audio_dir, f"chapter-{i:02d}.m4a"), "w").close()
    chapter_data_path = os.path.join(tmp_dir, "chapter_data.json")
    open(chapter_data_path, "w").close()
    rendition_manifest_path = os.path.join(tmp_dir, "rendition-manifest.json")
    open(rendition_manifest_path, "w").close()
    return audio_dir, chapter_data_path, rendition_manifest_path


def _make_direction_artifacts(tmp_dir: str) -> tuple[str, str]:
    character_registry_path = os.path.join(tmp_dir, "character_registry.json")
    voice_direction_path = os.path.join(tmp_dir, "voice_direction.json")
    open(character_registry_path, "w").close()
    open(voice_direction_path, "w").close()
    return character_registry_path, voice_direction_path


def _make_synthesis_units(audio_dir: str, n_chapters: int = 1) -> None:
    for i in range(1, n_chapters + 1):
        open(os.path.join(audio_dir, f"chapter-{i:02d}.synthesis_units.json"), "w").close()


def _make_run_context(tmp_dir: str) -> str:
    run_context_path = os.path.join(tmp_dir, "run.json")
    open(run_context_path, "w").close()
    return run_context_path


# ---------------------------------------------------------------------------
# make_client / key_exists — basic plumbing
# ---------------------------------------------------------------------------


class TestMakeClient(unittest.TestCase):

    @patch("openshelf.pipeline.r2.boto3.client")
    def test_creates_s3_client(self, mock_boto3_client):
        make_client()
        self.assertEqual(mock_boto3_client.call_args[0][0], "s3")

    @patch("openshelf.pipeline.r2.R2_ACCOUNT_ID", "abc123")
    @patch("openshelf.pipeline.r2.boto3.client")
    def test_uses_r2_endpoint(self, mock_boto3_client):
        make_client()
        kwargs = mock_boto3_client.call_args[1]
        self.assertIn("abc123", kwargs["endpoint_url"])

    @patch("openshelf.pipeline.r2.R2_ACCESS_KEY", "ak")
    @patch("openshelf.pipeline.r2.R2_SECRET_KEY", "sk")
    @patch("openshelf.pipeline.r2.R2_ACCOUNT_ID", "abc123")
    @patch("openshelf.pipeline.r2.boto3.client")
    def test_uses_credentials(self, mock_boto3_client):
        make_client()
        kwargs = mock_boto3_client.call_args[1]
        self.assertEqual(kwargs["aws_access_key_id"], "ak")
        self.assertEqual(kwargs["aws_secret_access_key"], "sk")


class TestKeyExists(unittest.TestCase):

    def test_returns_true_when_present(self):
        client = MagicMock()
        client.head_object.return_value = {}
        self.assertTrue(key_exists(client, BUCKET, "x/y.json"))

    def test_returns_false_on_404(self):
        from botocore.exceptions import ClientError
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject",
        )
        self.assertFalse(key_exists(client, BUCKET, "x/y.json"))

    def test_raises_on_other_client_errors(self):
        from botocore.exceptions import ClientError
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject",
        )
        with self.assertRaises(ClientError):
            key_exists(client, BUCKET, "x/y.json")


# ---------------------------------------------------------------------------
# upload_rendition_build
# ---------------------------------------------------------------------------


class TestUploadRenditionBuildKeys(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_uploads_audio_chapter_data_and_manifest(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=2)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        # 2 m4a + 1 chapter_data + 1 rendition-manifest = 4 uploads
        self.assertEqual(client.upload_file.call_count, 4)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_m4a_keys_under_build_prefix(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=2)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        keys = [c[0][2] for c in client.upload_file.call_args_list]
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/chapter-01.m4a",
            keys,
        )
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/chapter-02.m4a",
            keys,
        )

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_chapter_data_key_under_build_prefix(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        keys = [c[0][2] for c in client.upload_file.call_args_list]
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/chapter_data.json",
            keys,
        )

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_synthesis_units_keys_under_build_prefix_when_present(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=2)
            _make_synthesis_units(audio_dir, n_chapters=2)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        keys = [c[0][2] for c in client.upload_file.call_args_list]
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/chapter-01.synthesis_units.json",
            keys,
        )
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/chapter-02.synthesis_units.json",
            keys,
        )

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_rendition_manifest_key_under_build_prefix(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        keys = [c[0][2] for c in client.upload_file.call_args_list]
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/rendition-manifest.json",
            keys,
        )

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_returns_uploaded_keys(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=2)
            keys = upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        self.assertEqual(len(keys), 4)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_uploads_character_registry_and_voice_direction_when_provided(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=1)
            registry, direction = _make_direction_artifacts(tmp)
            keys = upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
                character_registry_path=registry,
                voice_direction_path=direction,
            )
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/character_registry.json",
            keys,
        )
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/voice_direction.json",
            keys,
        )
        self.assertEqual(client.upload_file.call_count, 5)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_uploads_run_context_when_provided(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=1)
            run_context = _make_run_context(tmp)
            keys = upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
                run_context_path=run_context,
            )
        self.assertIn(
            "books/kafka/the-trial/audio/kokoro-af-heart/builds/2a4f9c1/run.json",
            keys,
        )
        self.assertEqual(client.upload_file.call_count, 4)


class TestUploadRenditionBuildOrdering(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_rendition_manifest_is_uploaded_last(self, _exists):
        """rendition-manifest.json is the single-gate completion signal; it
        must be the final write so its presence guarantees the rest is on R2."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=3)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        last_key = client.upload_file.call_args_list[-1][0][2]
        self.assertTrue(last_key.endswith("rendition-manifest.json"))


class TestUploadRenditionBuildIdempotency(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_skips_entire_build_when_rendition_manifest_exists(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=5)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        client.upload_file.assert_not_called()

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_returns_empty_list_when_already_uploaded(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp)
            result = upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        self.assertEqual(result, [])

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_single_head_request(self, exists):
        """O(1) HEAD requests regardless of chapter count — single gate on
        rendition-manifest existence, not one HEAD per file."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=10)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        exists.assert_called_once()

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_force_bypasses_idempotency_gate(self, exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=2)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm, force=True,
            )
        # With force, the existence check is skipped entirely.
        exists.assert_not_called()
        self.assertEqual(client.upload_file.call_count, 4)


class TestUploadRenditionBuildHeaders(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_m4a_content_type_and_immutable_and_inline(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp, n_chapters=2)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        for c in client.upload_file.call_args_list:
            if c[0][2].endswith(".m4a"):
                extra = c[1]["ExtraArgs"]
                self.assertEqual(extra["ContentType"], "audio/mp4")
                self.assertEqual(extra["CacheControl"], R2_CACHE_CONTROL_IMMUTABLE)
                self.assertEqual(extra["ContentDisposition"], "inline")

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_json_artifacts_immutable_application_json(self, _exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, cd, rm = _make_build_dir(tmp)
            upload_rendition_build(
                client, BUCKET, AUTHOR, TITLE, RENDITION, BUILD,
                audio_dir, cd, rm,
            )
        json_calls = [c for c in client.upload_file.call_args_list if c[0][2].endswith(".json")]
        self.assertTrue(len(json_calls) >= 2)
        for c in json_calls:
            extra = c[1]["ExtraArgs"]
            self.assertEqual(extra["ContentType"], "application/json")
            self.assertEqual(extra["CacheControl"], R2_CACHE_CONTROL_IMMUTABLE)


# ---------------------------------------------------------------------------
# upload_book_manifest — always overwrites; no force flag; short cache
# ---------------------------------------------------------------------------


class TestUploadBookManifest(unittest.TestCase):

    def test_uploads_to_book_manifest_key(self):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            key = upload_book_manifest(client, BUCKET, AUTHOR, TITLE, tmp.name)
        self.assertEqual(key, "books/kafka/the-trial/manifest.json")
        client.upload_file.assert_called_once()

    def test_never_checks_existence(self):
        """Book manifest is the sole mutable pointer — always overwritten."""
        client = MagicMock()
        with patch("openshelf.pipeline.r2.key_exists") as exists, \
             tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            upload_book_manifest(client, BUCKET, AUTHOR, TITLE, tmp.name)
            exists.assert_not_called()

    def test_short_cache_control(self):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            upload_book_manifest(client, BUCKET, AUTHOR, TITLE, tmp.name)
        extra = client.upload_file.call_args[1]["ExtraArgs"]
        self.assertEqual(extra["CacheControl"], R2_CACHE_CONTROL_MANIFEST)

    def test_application_json_content_type(self):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            upload_book_manifest(client, BUCKET, AUTHOR, TITLE, tmp.name)
        extra = client.upload_file.call_args[1]["ExtraArgs"]
        self.assertEqual(extra["ContentType"], "application/json")


# ---------------------------------------------------------------------------
# upload_cover / upload_epub — root-level immutable artifacts
# ---------------------------------------------------------------------------


class TestUploadCover(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_jpg_key(self, _exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            key = upload_cover(client, BUCKET, AUTHOR, TITLE, tmp.name, "image/jpeg")
        self.assertEqual(key, "books/kafka/the-trial/cover.jpg")

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_png_key(self, _exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            key = upload_cover(client, BUCKET, AUTHOR, TITLE, tmp.name, "image/png")
        self.assertEqual(key, "books/kafka/the-trial/cover.png")

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_skips_if_exists(self, _exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            result = upload_cover(client, BUCKET, AUTHOR, TITLE, tmp.name)
        self.assertIsNone(result)
        client.upload_file.assert_not_called()

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_force_overwrites(self, exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            result = upload_cover(client, BUCKET, AUTHOR, TITLE, tmp.name, force=True)
        self.assertIsNotNone(result)
        exists.assert_not_called()
        client.upload_file.assert_called_once()


class TestUploadEpub(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_key_format(self, _exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
            key = upload_epub(client, BUCKET, "dostoevsky", "demons", tmp.name)
        self.assertEqual(key, "books/dostoevsky/demons/book.epub")

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_content_type_and_immutable(self, _exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
            upload_epub(client, BUCKET, AUTHOR, TITLE, tmp.name)
        extra = client.upload_file.call_args[1]["ExtraArgs"]
        self.assertEqual(extra["ContentType"], "application/epub+zip")
        self.assertEqual(extra["CacheControl"], R2_CACHE_CONTROL_IMMUTABLE)

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_skips_if_exists(self, _exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
            result = upload_epub(client, BUCKET, AUTHOR, TITLE, tmp.name)
        self.assertIsNone(result)
        client.upload_file.assert_not_called()

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_force_overwrites(self, exists):
        client = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
            result = upload_epub(client, BUCKET, AUTHOR, TITLE, tmp.name, force=True)
        self.assertIsNotNone(result)
        exists.assert_not_called()
        client.upload_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
