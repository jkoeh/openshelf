"""Tests for encoder.py — Step 4 of the pipeline."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.encoder import encode_to_mp3
from openshelf.config import MP3_BITRATE


def _mock_segment(duration_ms: int, frame_rate: int = 24000) -> MagicMock:
    """Create a mock AudioSegment with correct __len__ and frame_rate."""
    seg = MagicMock()
    seg.__len__ = MagicMock(return_value=duration_ms)
    seg.frame_rate = frame_rate
    return seg


class TestEncodeToMp3Basic(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_exports_mp3(self, mock_from_wav, mock_remove, mock_makedirs):
        seg = _mock_segment(60000)
        mock_from_wav.return_value = seg

        encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3")

        seg.export.assert_called_once_with("/tmp/ch.mp3", format="mp3", bitrate=MP3_BITRATE)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_returns_duration(self, mock_from_wav, mock_remove, mock_makedirs):
        mock_from_wav.return_value = _mock_segment(90000)

        duration = encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3")

        self.assertEqual(duration, 90.0)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_default_bitrate(self, mock_from_wav, mock_remove, mock_makedirs):
        seg = _mock_segment(1000)
        mock_from_wav.return_value = seg

        encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3")

        seg.export.assert_called_once_with("/tmp/ch.mp3", format="mp3", bitrate="128k")

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_custom_bitrate(self, mock_from_wav, mock_remove, mock_makedirs):
        seg = _mock_segment(1000)
        mock_from_wav.return_value = seg

        encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3", bitrate="192k")

        seg.export.assert_called_once_with("/tmp/ch.mp3", format="mp3", bitrate="192k")


class TestEncodeToMp3WavCleanup(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_deletes_wav_by_default(self, mock_from_wav, mock_remove, mock_makedirs):
        mock_from_wav.return_value = _mock_segment(1000)

        encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3")

        mock_remove.assert_called_once_with("/tmp/ch.wav")

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_keeps_wav_when_requested(self, mock_from_wav, mock_remove, mock_makedirs):
        mock_from_wav.return_value = _mock_segment(1000)

        encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3", delete_wav=False)

        mock_remove.assert_not_called()


class TestEncodeToMp3FileHandling(unittest.TestCase):

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_creates_output_directory(self, mock_from_wav, mock_remove, mock_makedirs):
        mock_from_wav.return_value = _mock_segment(1000)

        encode_to_mp3("/tmp/ch.wav", "/tmp/new/dir/ch.mp3")

        mock_makedirs.assert_called_once_with("/tmp/new/dir", exist_ok=True)

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_wav_not_found_raises(self, mock_from_wav, mock_makedirs):
        mock_from_wav.side_effect = FileNotFoundError("No such file")

        with self.assertRaises(FileNotFoundError):
            encode_to_mp3("/tmp/missing.wav", "/tmp/ch.mp3")

    @patch("openshelf.pipeline.encoder.os.makedirs")
    @patch("openshelf.pipeline.encoder.os.remove")
    @patch("openshelf.pipeline.encoder.AudioSegment.from_wav")
    def test_zero_duration(self, mock_from_wav, mock_remove, mock_makedirs):
        mock_from_wav.return_value = _mock_segment(0)

        duration = encode_to_mp3("/tmp/ch.wav", "/tmp/ch.mp3")

        self.assertEqual(duration, 0.0)


if __name__ == "__main__":
    unittest.main()
