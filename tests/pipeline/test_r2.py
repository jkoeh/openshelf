"""Tests for r2.py — Step 6 of the pipeline."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.r2 import make_client, key_exists, upload_book
from openshelf.config import R2_CACHE_CONTROL_AUDIO, R2_CACHE_CONTROL_MANIFEST


def _make_fake_audio_dir(tmp_dir: str, n_chapters: int = 2) -> tuple[str, str]:
    """Create fake MP3 files and manifest in tmp_dir. Returns (audio_dir, manifest_path)."""
    audio_dir = os.path.join(tmp_dir, "audio")
    os.makedirs(audio_dir)
    for i in range(1, n_chapters + 1):
        open(os.path.join(audio_dir, f"chapter-{i:02d}.mp3"), "w").close()
    manifest_path = os.path.join(tmp_dir, "manifest.json")
    open(manifest_path, "w").close()
    return audio_dir, manifest_path


class TestMakeClient(unittest.TestCase):

    @patch("openshelf.pipeline.r2.boto3.client")
    def test_creates_s3_client(self, mock_boto3_client):
        make_client()
        args = mock_boto3_client.call_args[0]
        self.assertEqual(args[0], "s3")

    @patch("openshelf.pipeline.r2.R2_ACCOUNT_ID", "abc123")
    @patch("openshelf.pipeline.r2.boto3.client")
    def test_uses_r2_endpoint(self, mock_boto3_client):
        make_client()
        kwargs = mock_boto3_client.call_args[1]
        self.assertIn("abc123", kwargs["endpoint_url"])

    @patch("openshelf.pipeline.r2.R2_ACCESS_KEY", "test-access-key")
    @patch("openshelf.pipeline.r2.R2_SECRET_KEY", "test-secret-key")
    @patch("openshelf.pipeline.r2.R2_ACCOUNT_ID", "abc123")
    @patch("openshelf.pipeline.r2.boto3.client")
    def test_uses_r2_credentials(self, mock_boto3_client):
        make_client()
        kwargs = mock_boto3_client.call_args[1]
        self.assertEqual(kwargs["aws_access_key_id"], "test-access-key")
        self.assertEqual(kwargs["aws_secret_access_key"], "test-secret-key")


class TestKeyExists(unittest.TestCase):

    def test_returns_true_when_key_exists(self):
        client = MagicMock()
        client.head_object.return_value = {}
        result = key_exists(client, "my-bucket", "some/key.mp3")
        self.assertTrue(result)

    def test_calls_head_object_with_correct_args(self):
        client = MagicMock()
        client.head_object.return_value = {}
        key_exists(client, "my-bucket", "audio/chapter-01.mp3")
        client.head_object.assert_called_once_with(Bucket="my-bucket", Key="audio/chapter-01.mp3")

    def test_returns_false_on_404(self):
        from botocore.exceptions import ClientError
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        result = key_exists(client, "my-bucket", "some/key.mp3")
        self.assertFalse(result)

    def test_raises_on_other_client_errors(self):
        from botocore.exceptions import ClientError
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )
        with self.assertRaises(ClientError):
            key_exists(client, "my-bucket", "some/key.mp3")


class TestUploadBookUploads(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_uploads_mp3_and_manifest(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        # 2 MP3s + 1 manifest
        self.assertEqual(client.upload_file.call_count, 3)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_r2_key_format_for_chapters(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        keys = [c[0][2] for c in client.upload_file.call_args_list]
        self.assertIn("kafka/the-trial/chapter-01.mp3", keys)
        self.assertIn("kafka/the-trial/chapter-02.mp3", keys)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_r2_key_format_for_manifest(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        keys = [c[0][2] for c in client.upload_file.call_args_list]
        self.assertIn("kafka/the-trial/manifest.json", keys)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_returns_uploaded_keys(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            keys = upload_book(client, "openshelf-audio", "kafka", "the-trial",
                               audio_dir, manifest_path)
        self.assertIsInstance(keys, list)
        self.assertEqual(len(keys), 3)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_manifest_uploaded_last(self, mock_exists):
        """manifest.json must be the final upload — it signals completion."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        last_key = client.upload_file.call_args_list[-1][0][2]
        self.assertTrue(last_key.endswith("manifest.json"))


class TestUploadBookIdempotency(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_skips_entire_book_if_manifest_exists(self, mock_exists):
        """Single HEAD on manifest — if present, skip all uploads."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=5)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        client.upload_file.assert_not_called()

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_returns_empty_list_when_manifest_exists(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp)
            keys = upload_book(client, "openshelf-audio", "kafka", "the-trial",
                               audio_dir, manifest_path)
        self.assertEqual(keys, [])

    @patch("openshelf.pipeline.r2.key_exists", return_value=True)
    def test_only_one_head_request(self, mock_exists):
        """O(1) HEAD requests regardless of chapter count — not O(N)."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=10)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        mock_exists.assert_called_once()


class TestUploadBookHeaders(unittest.TestCase):

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_mp3_content_type(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        mp3_calls = [c for c in client.upload_file.call_args_list
                     if c[0][2].endswith(".mp3")]
        self.assertTrue(len(mp3_calls) > 0)
        for c in mp3_calls:
            self.assertEqual(c[1]["ExtraArgs"]["ContentType"], "audio/mpeg")

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_manifest_content_type(self, mock_exists):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        manifest_call = next(c for c in client.upload_file.call_args_list
                             if c[0][2].endswith("manifest.json"))
        self.assertEqual(manifest_call[1]["ExtraArgs"]["ContentType"], "application/json")

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_mp3_cache_control(self, mock_exists):
        """MP3 files must have immutable cache — they never change once written."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        mp3_calls = [c for c in client.upload_file.call_args_list
                     if c[0][2].endswith(".mp3")]
        for c in mp3_calls:
            self.assertEqual(c[1]["ExtraArgs"]["CacheControl"], R2_CACHE_CONTROL_AUDIO)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_manifest_cache_control(self, mock_exists):
        """Manifest must have short TTL so updates propagate quickly."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        manifest_call = next(c for c in client.upload_file.call_args_list
                             if c[0][2].endswith("manifest.json"))
        self.assertEqual(manifest_call[1]["ExtraArgs"]["CacheControl"], R2_CACHE_CONTROL_MANIFEST)

    @patch("openshelf.pipeline.r2.key_exists", return_value=False)
    def test_mp3_content_disposition_inline(self, mock_exists):
        """Without inline disposition, browsers download instead of streaming."""
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir, manifest_path = _make_fake_audio_dir(tmp, n_chapters=2)
            upload_book(client, "openshelf-audio", "kafka", "the-trial",
                        audio_dir, manifest_path)
        mp3_calls = [c for c in client.upload_file.call_args_list
                     if c[0][2].endswith(".mp3")]
        for c in mp3_calls:
            self.assertEqual(c[1]["ExtraArgs"]["ContentDisposition"], "inline")


if __name__ == "__main__":
    unittest.main()
