"""Tests for encoder.py — Step 4 of the pipeline."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.encoder import audio_duration, encode_to_aac
from openshelf.config import AAC_BITRATE


def _mock_info(duration_secs: float, samplerate: int = 24000) -> MagicMock:
    """Create a mock soundfile info with frames and samplerate."""
    info = MagicMock()
    info.samplerate = samplerate
    info.frames = int(duration_secs * samplerate)
    return info


class TestEncodeToAacBasic(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_exports_m4a(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(60.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "ffmpeg")
        self.assertIn("/tmp/ch.wav", args)
        self.assertIn("/tmp/ch.m4a", args)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_uses_aac_codec(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(60.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a")

        args = mock_run.call_args[0][0]
        self.assertIn("-c:a", args)
        self.assertIn("aac", args)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_returns_duration(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(90.0)

        duration = encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a")

        self.assertEqual(duration, 90.0)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_default_bitrate(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(1.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a")

        args = mock_run.call_args[0][0]
        self.assertIn(AAC_BITRATE, args)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_custom_bitrate(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(1.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a", bitrate="32k")

        args = mock_run.call_args[0][0]
        self.assertIn("32k", args)


class TestEncodeToAacWavCleanup(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_deletes_wav_by_default(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(1.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a")

        mock_remove.assert_called_once_with("/tmp/ch.wav")

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_keeps_wav_when_requested(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(1.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a", delete_wav=False)

        mock_remove.assert_not_called()


class TestEncodeToAacFileHandling(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_creates_output_directory(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(1.0)

        encode_to_aac("/tmp/ch.wav", "/tmp/new/dir/ch.m4a")

        mock_makedirs.assert_called_once_with("/tmp/new/dir", exist_ok=True)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_wav_not_found_raises(self, mock_info, mock_run, mock_makedirs):
        mock_info.side_effect = FileNotFoundError("No such file")

        with self.assertRaises(FileNotFoundError):
            encode_to_aac("/tmp/missing.wav", "/tmp/ch.m4a")

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.subprocess.run")
    @patch("openshelf.pipeline.encoder.sf.info")
    def test_zero_duration(self, mock_info, mock_run, mock_remove, mock_makedirs):
        mock_info.return_value = _mock_info(0.0)

        duration = encode_to_aac("/tmp/ch.wav", "/tmp/ch.m4a")

        self.assertEqual(duration, 0.0)


class TestAudioDuration(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.subprocess.run")
    def test_returns_duration(self, mock_run):
        mock_run.return_value = MagicMock(stdout="123.456\n")

        result = audio_duration("/tmp/chapter-01.m4a")

        self.assertAlmostEqual(result, 123.456)

    @patch("openshelf.pipeline.encoder.subprocess.run")
    def test_calls_ffprobe(self, mock_run):
        mock_run.return_value = MagicMock(stdout="60.0\n")

        audio_duration("/tmp/chapter-01.m4a")

        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "ffprobe")
        self.assertIn("/tmp/chapter-01.m4a", args)

    @patch("openshelf.pipeline.encoder.subprocess.run")
    def test_ffprobe_not_found_raises(self, mock_run):
        mock_run.side_effect = FileNotFoundError("ffprobe not found")

        with self.assertRaises(FileNotFoundError):
            audio_duration("/tmp/chapter-01.m4a")

    @patch("openshelf.pipeline.encoder.subprocess.run")
    def test_ffprobe_failure_raises(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffprobe")

        with self.assertRaises(subprocess.CalledProcessError):
            audio_duration("/tmp/chapter-01.m4a")


if __name__ == "__main__":
    unittest.main()
