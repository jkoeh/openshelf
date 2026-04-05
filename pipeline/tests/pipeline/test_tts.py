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
    ChunkInfo,
    _generate_silence,
    _normalize,
    _apply_boundary_fades,
    _split_segments_at_prefix,
    _text_for_matching,
)
from openshelf.config import (
    TTS_LANGUAGE,
    TTS_VOICE,
    TTS_SAMPLE_RATE,
    SILENCE_PARAGRAPH_BREAK_MS,
    SILENCE_MID_PARAGRAPH_MS,
    CROSSFADE_MS,
)


def _fake_audio(n_samples: int, peak: float = 0.5) -> np.ndarray:
    """Return a numpy float32 array with a known peak."""
    audio = np.zeros(n_samples, dtype=np.float32)
    if n_samples > 0:
        audio[0] = peak
        audio[-1] = -peak
    return audio


def _make_chunks(texts, ends_paragraph=True, context_prefix=""):
    """Wrap plain strings into ChunkInfo objects for tests."""
    return [ChunkInfo(text=t, ends_paragraph=ends_paragraph, context_prefix=context_prefix) for t in texts]


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

        result = synthesize_chapter(pipeline, _make_chunks(["Hello world."]), "/tmp/out.wav")

        self.assertIsInstance(result, SynthesisResult)
        mock_sf_write.assert_called_once()

    @patch("openshelf.pipeline.tts.sf.write")
    def test_multiple_chunks_with_paragraph_break_silence(self, mock_sf_write):
        """Two chunks both ending paragraphs → paragraph break silence between them."""
        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(1000))])

        pipeline = MagicMock(side_effect=make_iter)
        chunks = [
            ChunkInfo(text="A.", ends_paragraph=True),
            ChunkInfo(text="B.", ends_paragraph=True),
            ChunkInfo(text="C.", ends_paragraph=True),
        ]

        synthesize_chapter(pipeline, chunks, "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        para_silence = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)
        fade_samples = int(TTS_SAMPLE_RATE * CROSSFADE_MS / 1000)
        # Each chunk is 1000 samples; fades don't change length
        expected_len = 3 * 1000 + 2 * para_silence
        self.assertEqual(len(written_audio), expected_len)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_mid_paragraph_gets_shorter_silence(self, mock_sf_write):
        """Chunks within the same paragraph get shorter silence."""
        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(1000))])

        pipeline = MagicMock(side_effect=make_iter)
        chunks = [
            ChunkInfo(text="A.", ends_paragraph=False),
            ChunkInfo(text="B.", ends_paragraph=True),
        ]

        synthesize_chapter(pipeline, chunks, "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        mid_silence = int(TTS_SAMPLE_RATE * SILENCE_MID_PARAGRAPH_MS / 1000)
        expected_len = 2 * 1000 + mid_silence
        self.assertEqual(len(written_audio), expected_len)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_mixed_silence_durations(self, mock_sf_write):
        """Three chunks: first mid-para, second ends-para → different silence gaps."""
        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(1000))])

        pipeline = MagicMock(side_effect=make_iter)
        chunks = [
            ChunkInfo(text="A.", ends_paragraph=False),
            ChunkInfo(text="B.", ends_paragraph=True),
            ChunkInfo(text="C.", ends_paragraph=True),
        ]

        result = synthesize_chapter(pipeline, chunks, "/tmp/out.wav")

        mid_silence = int(TTS_SAMPLE_RATE * SILENCE_MID_PARAGRAPH_MS / 1000)
        para_silence = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)

        # Chunk 0 starts at 0
        self.assertAlmostEqual(result.chunk_audio_starts[0], 0.0)
        # Chunk 1 starts after chunk 0 audio + mid-paragraph silence
        expected_1 = (1000 + mid_silence) / TTS_SAMPLE_RATE
        self.assertAlmostEqual(result.chunk_audio_starts[1], expected_1)
        # Chunk 2 starts after chunk 1 audio + paragraph break silence
        expected_2 = (1000 + mid_silence + 1000 + para_silence) / TTS_SAMPLE_RATE
        self.assertAlmostEqual(result.chunk_audio_starts[2], expected_2)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_returns_synthesis_result(self, mock_sf_write):
        audio = _fake_audio(TTS_SAMPLE_RATE)  # 1 second
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", audio)])

        result = synthesize_chapter(pipeline, _make_chunks(["chunk"]), "/tmp/out.wav")

        self.assertIsInstance(result, SynthesisResult)
        self.assertEqual(result.skipped_chunks, 0)
        self.assertIsInstance(result.chunk_audio_starts, list)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_output_file_written(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(100))])

        synthesize_chapter(pipeline, _make_chunks(["x"]), "/tmp/chapter.wav")

        mock_sf_write.assert_called_once()
        args = mock_sf_write.call_args[0]
        self.assertEqual(args[0], "/tmp/chapter.wav")
        self.assertEqual(args[2], TTS_SAMPLE_RATE)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_voice_passed_to_pipeline(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(100))])

        synthesize_chapter(pipeline, _make_chunks(["text"]), "/tmp/out.wav")

        pipeline.assert_called_with("text", voice=TTS_VOICE)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_custom_voice(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(100))])

        synthesize_chapter(pipeline, _make_chunks(["text"]), "/tmp/out.wav", voice="bf_emma")

        pipeline.assert_called_with("text", voice="bf_emma")

    @patch("openshelf.pipeline.tts.sf.write")
    def test_chunks_are_normalized(self, mock_sf_write):
        # Use audio with peak in the middle (not at edges, which get faded)
        audio = np.zeros(1000, dtype=np.float32)
        audio[500] = 0.5
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", audio)])

        synthesize_chapter(pipeline, _make_chunks(["chunk"]), "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        peak = np.abs(written_audio).max()
        # Peak at sample 500 is outside fade region, so it should be normalized to 0.89
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
            if "bad" in text:
                raise RuntimeError("TTS error")
            return iter([("g", "p", good_audio)])

        pipeline = MagicMock(side_effect=side_effect)

        result = synthesize_chapter(
            pipeline,
            [ChunkInfo(text="bad"), ChunkInfo(text="good")],
            "/tmp/out.wav",
        )

        self.assertEqual(result.skipped_chunks, 1)
        mock_sf_write.assert_called_once()

    def test_all_chunks_fail_raises(self):
        pipeline = MagicMock(side_effect=RuntimeError("TTS error"))

        with self.assertRaises(RuntimeError):
            synthesize_chapter(pipeline, _make_chunks(["a", "b"]), "/tmp/out.wav")


class TestSynthesizeChapterAudioStarts(unittest.TestCase):

    @patch("openshelf.pipeline.tts.sf.write")
    def test_single_chunk_starts_at_zero(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(1000))])

        result = synthesize_chapter(pipeline, _make_chunks(["chunk"]), "/tmp/out.wav")

        self.assertEqual(result.chunk_audio_starts, [0.0])

    @patch("openshelf.pipeline.tts.sf.write")
    def test_two_chunks_correct_offsets(self, mock_sf_write):
        chunk_samples = 1000
        para_silence = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)

        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(chunk_samples))])

        pipeline = MagicMock(side_effect=make_iter)
        result = synthesize_chapter(pipeline, _make_chunks(["A.", "B."]), "/tmp/out.wav")

        self.assertAlmostEqual(result.chunk_audio_starts[0], 0.0)
        expected_start1 = (chunk_samples + para_silence) / TTS_SAMPLE_RATE
        self.assertAlmostEqual(result.chunk_audio_starts[1], expected_start1)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_chunk_audio_starts_length_matches_input(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(100))])

        pipeline = MagicMock(side_effect=make_iter)
        result = synthesize_chapter(pipeline, _make_chunks(["A.", "B.", "C."]), "/tmp/out.wav")

        self.assertEqual(len(result.chunk_audio_starts), 3)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_skipped_chunk_gets_negative_one(self, mock_sf_write):
        def side_effect(text, voice=None):
            if "bad" in text:
                raise RuntimeError("TTS error")
            return iter([("g", "p", _fake_audio(1000))])

        pipeline = MagicMock(side_effect=side_effect)
        result = synthesize_chapter(
            pipeline,
            [ChunkInfo(text="bad"), ChunkInfo(text="good")],
            "/tmp/out.wav",
        )

        self.assertEqual(result.chunk_audio_starts[0], -1.0)
        self.assertEqual(result.chunk_audio_starts[1], 0.0)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_offsets_accumulate_correctly_three_chunks(self, mock_sf_write):
        samples_per_chunk = [500, 800, 300]
        para_silence = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)
        call_idx = [0]

        def make_iter(text, voice=None):
            idx = call_idx[0]
            call_idx[0] += 1
            return iter([("g", "p", _fake_audio(samples_per_chunk[idx]))])

        pipeline = MagicMock(side_effect=make_iter)
        result = synthesize_chapter(pipeline, _make_chunks(["A.", "B.", "C."]), "/tmp/out.wav")

        self.assertAlmostEqual(result.chunk_audio_starts[0], 0.0)
        self.assertAlmostEqual(
            result.chunk_audio_starts[1],
            (samples_per_chunk[0] + para_silence) / TTS_SAMPLE_RATE,
        )
        self.assertAlmostEqual(
            result.chunk_audio_starts[2],
            (samples_per_chunk[0] + para_silence + samples_per_chunk[1] + para_silence) / TTS_SAMPLE_RATE,
        )


class TestContextOverlap(unittest.TestCase):

    @patch("openshelf.pipeline.tts.sf.write")
    def test_context_prefix_trimmed_from_audio(self, mock_sf_write):
        """When context_prefix is set, the synthesized text is longer but output is trimmed."""
        prefix = "Previous sentence one."
        real_text = "This is the real content."

        def make_iter(text, voice=None):
            # Return one segment per sentence (split on ". ")
            parts = text.replace(". ", ".\x00").split("\x00")
            return iter([(p, "p", _fake_audio(len(p.split()) * 1000)) for p in parts if p.strip()])

        pipeline = MagicMock(side_effect=make_iter)
        chunks = [
            ChunkInfo(text="First chunk.", ends_paragraph=True),
            ChunkInfo(text=real_text, ends_paragraph=True, context_prefix=prefix),
        ]
        result = synthesize_chapter(pipeline, chunks, "/tmp/out.wav")

        # Pipeline should be called with the combined text for chunk 2
        combined_text = prefix + " " + real_text
        calls = [c.args[0] for c in pipeline.call_args_list]
        self.assertEqual(calls[1], combined_text)

        # chunk_audio_starts should have 2 entries
        self.assertEqual(len(result.chunk_audio_starts), 2)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_no_context_prefix_on_first_chunk(self, mock_sf_write):
        """First chunk with no context_prefix is synthesized as-is."""
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(1000))])

        chunks = [ChunkInfo(text="Only chunk.", context_prefix="")]
        synthesize_chapter(pipeline, chunks, "/tmp/out.wav")

        pipeline.assert_called_with("Only chunk.", voice=TTS_VOICE)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_context_overlap_trims_prefix_segments(self, mock_sf_write):
        """Prefix segments are dropped — only real content audio is kept."""
        prefix = "Context sentence here."
        real_text = "Real content here."
        call_idx = [0]

        def make_iter(text, voice=None):
            call_idx[0] += 1
            if call_idx[0] == 1:
                # First chunk: single segment
                return iter([("First chunk.", "p", _fake_audio(3000))])
            else:
                # Second chunk (with prefix): two segments
                return iter([
                    ("Context sentence here.", "p", _fake_audio(3000)),
                    ("Real content here.", "p", _fake_audio(3000)),
                ])

        pipeline = MagicMock(side_effect=make_iter)
        chunks = [
            ChunkInfo(text="First chunk.", ends_paragraph=True),
            ChunkInfo(text=real_text, ends_paragraph=True, context_prefix=prefix),
        ]
        result = synthesize_chapter(pipeline, chunks, "/tmp/out.wav")

        written_audio = mock_sf_write.call_args[0][1]
        para_silence = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)
        # Chunk 1: 3000 samples
        # Chunk 2: prefix (3000) dropped, real (3000) kept
        # Total: 3000 + silence + 3000
        expected = 3000 + para_silence + 3000
        # Allow some tolerance for fading
        self.assertAlmostEqual(len(written_audio), expected, delta=500)


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

    def test_paragraph_break_silence_length(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_PARAGRAPH_BREAK_MS)
        expected = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)
        self.assertEqual(len(silence), expected)

    def test_mid_paragraph_silence_length(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_MID_PARAGRAPH_MS)
        expected = int(TTS_SAMPLE_RATE * SILENCE_MID_PARAGRAPH_MS / 1000)
        self.assertEqual(len(silence), expected)

    def test_silence_is_zeros(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_PARAGRAPH_BREAK_MS)
        self.assertTrue(np.all(silence == 0.0))

    def test_silence_dtype(self):
        silence = _generate_silence(TTS_SAMPLE_RATE, SILENCE_PARAGRAPH_BREAK_MS)
        self.assertEqual(silence.dtype, np.float32)


class TestApplyBoundaryFades(unittest.TestCase):

    def test_fade_in_applied(self):
        audio = np.ones(1000, dtype=np.float32)
        result = _apply_boundary_fades(audio, TTS_SAMPLE_RATE)
        # First sample should be near zero (faded in from 0)
        self.assertAlmostEqual(result[0], 0.0, places=3)

    def test_fade_out_applied(self):
        audio = np.ones(1000, dtype=np.float32)
        result = _apply_boundary_fades(audio, TTS_SAMPLE_RATE)
        # Last sample should be near zero (faded out to 0)
        self.assertAlmostEqual(result[-1], 0.0, places=3)

    def test_does_not_change_length(self):
        audio = np.ones(1000, dtype=np.float32)
        result = _apply_boundary_fades(audio, TTS_SAMPLE_RATE)
        self.assertEqual(len(result), 1000)

    def test_short_audio_returned_unchanged(self):
        fade_samples = int(TTS_SAMPLE_RATE * CROSSFADE_MS / 1000)
        # Audio shorter than 2 * fade_samples
        audio = np.ones(fade_samples, dtype=np.float32)
        result = _apply_boundary_fades(audio, TTS_SAMPLE_RATE)
        np.testing.assert_array_equal(result, audio)

    def test_does_not_modify_original(self):
        audio = np.ones(1000, dtype=np.float32)
        original = audio.copy()
        _apply_boundary_fades(audio, TTS_SAMPLE_RATE)
        np.testing.assert_array_equal(audio, original)


class TestSplitSegmentsAtPrefix(unittest.TestCase):

    def test_drops_prefix_segments(self):
        """Segments covering the prefix are dropped, only real content kept."""
        results = [
            ("Hello world.", "p", _fake_audio(5000)),  # prefix segment
            ("Real content here.", "p", _fake_audio(3000)),  # real segment
        ]
        audio = _split_segments_at_prefix(results, "Hello world.", TTS_SAMPLE_RATE)
        # Only the second segment's 3000 samples should remain
        self.assertEqual(len(audio), 3000)

    def test_multiple_prefix_segments(self):
        """Multiple segments can be part of the prefix."""
        results = [
            ("First prefix.", "p", _fake_audio(2000)),
            ("Second prefix.", "p", _fake_audio(2000)),
            ("Real content.", "p", _fake_audio(3000)),
        ]
        audio = _split_segments_at_prefix(
            results, "First prefix. Second prefix.", TTS_SAMPLE_RATE
        )
        self.assertEqual(len(audio), 3000)

    def test_single_segment_falls_back(self):
        """When all text is in one segment, falls back to proportional trim."""
        results = [
            ("Hello world real content.", "p", _fake_audio(10000)),
        ]
        audio = _split_segments_at_prefix(results, "Hello world", TTS_SAMPLE_RATE)
        # Should still return something (proportional fallback)
        self.assertGreater(len(audio), 0)
        self.assertLess(len(audio), 10000)

    def test_fade_in_applied(self):
        """A fade-in is applied at the start of the real content."""
        results = [
            ("Prefix text.", "p", _fake_audio(2000)),
            ("Real text.", "p", np.ones(3000, dtype=np.float32)),
        ]
        audio = _split_segments_at_prefix(results, "Prefix text.", TTS_SAMPLE_RATE)
        # First sample should be near zero (faded in)
        self.assertAlmostEqual(audio[0], 0.0, places=3)

    def test_no_prefix_returns_all(self):
        """Empty prefix returns all segments concatenated."""
        results = [
            ("Hello.", "p", _fake_audio(2000)),
            ("World.", "p", _fake_audio(3000)),
        ]
        audio = _split_segments_at_prefix(results, "", TTS_SAMPLE_RATE)
        self.assertEqual(len(audio), 5000)


class TestTextForMatching(unittest.TestCase):

    def test_strips_punctuation(self):
        self.assertEqual(_text_for_matching("Hello, world!"), "helloworld")

    def test_case_insensitive(self):
        self.assertEqual(_text_for_matching("ABC"), _text_for_matching("abc"))

    def test_strips_whitespace(self):
        self.assertEqual(_text_for_matching("a  b  c"), "abc")


if __name__ == "__main__":
    unittest.main()
