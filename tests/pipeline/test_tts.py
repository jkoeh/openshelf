"""Tests for tts.py — Step 3 of the pipeline."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import numpy as np

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.tts import (
    get_device,
    load_pipeline,
    synthesize_chapter,
    SynthesisResult,
    _generate_silence,
    _normalize,
)
from openshelf.config import (
    TTS_LANGUAGE,
    TTS_VOICE,
    TTS_SAMPLE_RATE,
    SILENCE_BETWEEN_CHUNKS_MS,
)


def _fake_audio(n_samples: int, peak: float = 0.5) -> np.ndarray:
    """Return a numpy float32 array with a known peak."""
    audio = np.zeros(n_samples, dtype=np.float32)
    if n_samples > 0:
        audio[0] = peak
        audio[-1] = -peak
    return audio


class TestGetDevice(unittest.TestCase):

    def test_cuda_when_available(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch("openshelf.pipeline.tts._import_torch", return_value=mock_torch):
            self.assertEqual(get_device(), "cuda")

    def test_mps_when_no_cuda(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = True
        with patch("openshelf.pipeline.tts._import_torch", return_value=mock_torch):
            self.assertEqual(get_device(), "mps")

    def test_cpu_fallback(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False
        with patch("openshelf.pipeline.tts._import_torch", return_value=mock_torch):
            self.assertEqual(get_device(), "cpu")


class TestLoadPipeline(unittest.TestCase):

    def test_loads_with_language(self):
        mock_kp_cls = MagicMock()
        with patch("openshelf.pipeline.tts._import_kpipeline", return_value=mock_kp_cls):
            load_pipeline(device="cpu")
            mock_kp_cls.assert_called_once_with(lang_code=TTS_LANGUAGE, device="cpu")

    def test_uses_provided_device(self):
        mock_kp_cls = MagicMock()
        with patch("openshelf.pipeline.tts._import_kpipeline", return_value=mock_kp_cls):
            pipeline = load_pipeline(device="cpu")
            self.assertIsNotNone(pipeline)

    @patch("openshelf.pipeline.tts.get_device", return_value="mps")
    def test_uses_auto_device(self, mock_get_device):
        mock_kp_cls = MagicMock()
        with patch("openshelf.pipeline.tts._import_kpipeline", return_value=mock_kp_cls):
            load_pipeline()
            mock_get_device.assert_called_once()
            mock_kp_cls.assert_called_once_with(lang_code=TTS_LANGUAGE, device="mps")


class TestSynthesizeChapter(unittest.TestCase):

    @patch("openshelf.pipeline.tts.sf.write")
    def test_single_chunk(self, mock_sf_write):
        audio = _fake_audio(24000)
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", audio)])

        result = synthesize_chapter(pipeline, ["Hello world."], "/tmp/out.wav")

        self.assertIsInstance(result, SynthesisResult)
        mock_sf_write.assert_called_once()
        written_audio = mock_sf_write.call_args[0][1]
        self.assertEqual(len(written_audio), 24000)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_multiple_chunks_with_silence(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(1000))])

        pipeline = MagicMock(side_effect=make_iter)

        synthesize_chapter(pipeline, ["A.", "B.", "C."], "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        silence_samples = int(TTS_SAMPLE_RATE * SILENCE_BETWEEN_CHUNKS_MS / 1000)
        expected_len = 3 * 1000 + 2 * silence_samples
        self.assertEqual(len(written_audio), expected_len)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_silence_duration_correct(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(100))])

        pipeline = MagicMock(side_effect=make_iter)

        synthesize_chapter(pipeline, ["A.", "B."], "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        silence_samples = int(TTS_SAMPLE_RATE * SILENCE_BETWEEN_CHUNKS_MS / 1000)
        self.assertEqual(len(written_audio), 200 + silence_samples)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_returns_synthesis_result(self, mock_sf_write):
        audio = _fake_audio(TTS_SAMPLE_RATE)  # 1 second
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", audio)])

        result = synthesize_chapter(pipeline, ["chunk"], "/tmp/out.wav")

        self.assertIsInstance(result, SynthesisResult)
        self.assertAlmostEqual(result.duration_seconds, 1.0, places=2)
        self.assertEqual(result.skipped_chunks, 0)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_output_file_written(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(100))])

        synthesize_chapter(pipeline, ["x"], "/tmp/chapter.wav")

        mock_sf_write.assert_called_once()
        args = mock_sf_write.call_args[0]
        self.assertEqual(args[0], "/tmp/chapter.wav")
        self.assertEqual(args[2], TTS_SAMPLE_RATE)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_voice_passed_to_pipeline(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(100))])

        synthesize_chapter(pipeline, ["text"], "/tmp/out.wav")

        pipeline.assert_called_with("text", voice=TTS_VOICE)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_custom_voice(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(100))])

        synthesize_chapter(pipeline, ["text"], "/tmp/out.wav", voice="bf_emma")

        pipeline.assert_called_with("text", voice="bf_emma")

    @patch("openshelf.pipeline.tts.sf.write")
    def test_chunks_are_normalized(self, mock_sf_write):
        audio = _fake_audio(1000, peak=0.5)
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", audio)])

        synthesize_chapter(pipeline, ["chunk"], "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        peak = np.abs(written_audio).max()
        self.assertAlmostEqual(peak, 0.89, places=2)


class TestSynthesizeChapterErrors(unittest.TestCase):

    def test_empty_chunks_raises(self):
        pipeline = MagicMock()
        with self.assertRaises(ValueError):
            synthesize_chapter(pipeline, [], "/tmp/out.wav")

    @patch("openshelf.pipeline.tts.sf.write")
    def test_failed_chunk_skipped(self, mock_sf_write):
        call_count = [0]
        good_audio = _fake_audio(1000)

        def side_effect(text, voice=None):
            call_count[0] += 1
            if text == "bad":
                raise RuntimeError("TTS error")
            return iter([("g", "p", good_audio)])

        pipeline = MagicMock(side_effect=side_effect)

        result = synthesize_chapter(pipeline, ["bad", "good"], "/tmp/out.wav")

        self.assertEqual(result.skipped_chunks, 1)
        mock_sf_write.assert_called_once()

    def test_all_chunks_fail_raises(self):
        pipeline = MagicMock(side_effect=RuntimeError("TTS error"))

        with self.assertRaises(RuntimeError):
            synthesize_chapter(pipeline, ["a", "b"], "/tmp/out.wav")


class TestNormalize(unittest.TestCase):

    def test_normalize_scales_to_target(self):
        audio = _fake_audio(100, peak=0.5)
        result = _normalize(audio)
        self.assertAlmostEqual(np.abs(result).max(), 0.89, places=5)

    def test_normalize_silent_audio(self):
        audio = np.zeros(100, dtype=np.float32)
        result = _normalize(audio)
        self.assertTrue(np.all(result == 0))

    def test_normalize_already_at_target(self):
        audio = _fake_audio(100, peak=0.89)
        result = _normalize(audio)
        np.testing.assert_array_almost_equal(result, audio)


class TestGenerateSilence(unittest.TestCase):

    def test_silence_length(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_BETWEEN_CHUNKS_MS)
        expected = int(TTS_SAMPLE_RATE * SILENCE_BETWEEN_CHUNKS_MS / 1000)
        self.assertEqual(len(silence), expected)
        self.assertEqual(len(silence), 9600)

    def test_silence_is_zeros(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_BETWEEN_CHUNKS_MS)
        self.assertTrue(np.all(silence == 0.0))

    def test_silence_dtype(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_BETWEEN_CHUNKS_MS)
        self.assertEqual(silence.dtype, np.float32)


if __name__ == "__main__":
    unittest.main()
