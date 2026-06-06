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
    WordTimestamp,
    _generate_silence,
    _normalize,
    _apply_boundary_fades,
    _limit_boundary_silence,
    _extract_words,
)
from openshelf.pipeline.tts_engine import DirectedSegment, PostProcessingConfig, TTSCapabilities, VoiceSpec
from openshelf.config import (
    TTS_LANGUAGE,
    TTS_VOICE,
    TTS_SAMPLE_RATE,
    SILENCE_PARAGRAPH_BREAK_MS,
    SILENCE_MID_PARAGRAPH_MS,
    CROSSFADE_MS,
    LEAD_IN_SILENCE_MS,
)


_LEAD_IN_SAMPLES = int(TTS_SAMPLE_RATE * LEAD_IN_SILENCE_MS / 1000)


def _fake_audio(n_samples: int, peak: float = 0.5) -> np.ndarray:
    """Return a numpy float32 array with a known peak."""
    audio = np.zeros(n_samples, dtype=np.float32)
    if n_samples > 0:
        audio[0] = peak
        audio[-1] = -peak
    return audio


def _make_chunks(texts, ends_paragraph=True):
    """Wrap plain strings into ChunkInfo objects for tests."""
    return [ChunkInfo(text=t, ends_paragraph=ends_paragraph) for t in texts]


class _FakeAligner:
    def align(self, audio, text, sample_rate):
        return [WordTimestamp(word=w, start=i * 0.1, end=i * 0.1 + 0.05)
                for i, w in enumerate(text.split())]


class _RecordingAligner(_FakeAligner):
    def __init__(self):
        self.texts = []

    def align(self, audio, text, sample_rate):
        self.texts.append(text)
        return super().align(audio, text, sample_rate)


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
        expected_len = _LEAD_IN_SAMPLES + 3 * 1000 + 2 * para_silence
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
        expected_len = _LEAD_IN_SAMPLES + 2 * 1000 + mid_silence
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

        # Chunk 0 starts after the lead-in silence
        self.assertAlmostEqual(result.chunk_audio_starts[0], _LEAD_IN_SAMPLES / TTS_SAMPLE_RATE)
        # Chunk 1 starts after chunk 0 audio + mid-paragraph silence
        expected_1 = (_LEAD_IN_SAMPLES + 1000 + mid_silence) / TTS_SAMPLE_RATE
        self.assertAlmostEqual(result.chunk_audio_starts[1], expected_1)
        # Chunk 2 starts after chunk 1 audio + paragraph break silence
        expected_2 = (_LEAD_IN_SAMPLES + 1000 + mid_silence + 1000 + para_silence) / TTS_SAMPLE_RATE
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
    def test_single_chunk_starts_after_lead_in(self, mock_sf_write):
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", _fake_audio(1000))])

        result = synthesize_chapter(pipeline, _make_chunks(["chunk"]), "/tmp/out.wav")

        self.assertEqual(result.chunk_audio_starts, [_LEAD_IN_SAMPLES / TTS_SAMPLE_RATE])

    @patch("openshelf.pipeline.tts.sf.write")
    def test_two_chunks_correct_offsets(self, mock_sf_write):
        chunk_samples = 1000
        para_silence = int(TTS_SAMPLE_RATE * SILENCE_PARAGRAPH_BREAK_MS / 1000)

        def make_iter(text, voice=None):
            return iter([("g", "p", _fake_audio(chunk_samples))])

        pipeline = MagicMock(side_effect=make_iter)
        result = synthesize_chapter(pipeline, _make_chunks(["A.", "B."]), "/tmp/out.wav")

        self.assertAlmostEqual(result.chunk_audio_starts[0], _LEAD_IN_SAMPLES / TTS_SAMPLE_RATE)
        expected_start1 = (_LEAD_IN_SAMPLES + chunk_samples + para_silence) / TTS_SAMPLE_RATE
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
        self.assertAlmostEqual(result.chunk_audio_starts[1], _LEAD_IN_SAMPLES / TTS_SAMPLE_RATE)

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

        self.assertAlmostEqual(result.chunk_audio_starts[0], _LEAD_IN_SAMPLES / TTS_SAMPLE_RATE)
        self.assertAlmostEqual(
            result.chunk_audio_starts[1],
            (_LEAD_IN_SAMPLES + samples_per_chunk[0] + para_silence) / TTS_SAMPLE_RATE,
        )
        self.assertAlmostEqual(
            result.chunk_audio_starts[2],
            (_LEAD_IN_SAMPLES + samples_per_chunk[0] + para_silence + samples_per_chunk[1] + para_silence) / TTS_SAMPLE_RATE,
        )


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


class _FakeToken:
    """Mock Kokoro MToken for testing.

    The `whitespace` attribute holds the whitespace *following* this token —
    a non-empty value marks the end of a word. Defaults to a single space so
    each token represents its own word in tests that don't care about the
    sub-word grouping behavior.
    """
    def __init__(self, text, start_ts, end_ts, whitespace=" "):
        self.text = text
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.whitespace = whitespace


class _FakeResult:
    """Mock Kokoro Result for testing."""
    def __init__(self, graphemes, phonemes, audio, tokens=None):
        self.graphemes = graphemes
        self.phonemes = phonemes
        self.audio = audio
        self.tokens = tokens or []

    def __iter__(self):
        return iter((self.graphemes, self.phonemes, self.audio))


class TestExtractWords(unittest.TestCase):

    def test_basic_extraction(self):
        results = [
            _FakeResult("Hello world.", "p", _fake_audio(2000), [
                _FakeToken("Hello", 0.0, 0.3),
                _FakeToken("world.", 0.3, 0.6),
            ]),
        ]
        words = _extract_words(results)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0].word, "Hello")
        self.assertAlmostEqual(words[0].start, 0.0)
        self.assertEqual(words[1].word, "world.")
        self.assertAlmostEqual(words[1].start, 0.3)

    def test_skips_tokens_without_timestamps(self):
        results = [
            _FakeResult("Hello.", "p", _fake_audio(1000), [
                _FakeToken("Hello", 0.0, 0.3),
                _FakeToken(",", None, None),  # punctuation
                _FakeToken("world", 0.3, 0.6),
            ]),
        ]
        words = _extract_words(results)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0].word, "Hello")
        self.assertEqual(words[1].word, "world")

    def test_empty_results(self):
        words = _extract_words([])
        self.assertEqual(words, [])

    def test_results_without_tokens(self):
        """Tuple-style results (no .tokens) return empty words."""
        results = [("graphemes", "phonemes", _fake_audio(1000))]
        words = _extract_words(results)
        self.assertEqual(words, [])

    def test_multiple_results_offsets_accumulate(self):
        """Tokens in later Results are offset by the running audio duration."""
        # Each Result has 24000 samples = 1.0s of audio at TTS_SAMPLE_RATE.
        n = TTS_SAMPLE_RATE
        results = [
            _FakeResult("First.", "p", _fake_audio(n), [
                _FakeToken("First", 0.0, 0.3, whitespace=""),
                _FakeToken(".",     0.3, 0.4, whitespace=" "),
            ]),
            _FakeResult("Second.", "p", _fake_audio(n), [
                # Per-Result reference frame: starts again from 0.0
                _FakeToken("Second", 0.0, 0.3, whitespace=""),
                _FakeToken(".",      0.3, 0.4, whitespace=" "),
            ]),
        ]
        words = _extract_words(results)
        self.assertEqual([w.word for w in words], ["First.", "Second."])
        # First word: no offset
        self.assertAlmostEqual(words[0].start, 0.0)
        self.assertAlmostEqual(words[0].end, 0.4)
        # Second word: offset by Result 0's 1.0s of audio
        self.assertAlmostEqual(words[1].start, 1.0)
        self.assertAlmostEqual(words[1].end, 1.4)

    def test_groups_sub_word_tokens_by_whitespace(self):
        """A contraction split across multiple MTokens collapses into one word."""
        # "don't go" — misaki splits "don't" into ["don", "'", "t"], with
        # whitespace only on the final "t" token.
        results = [
            _FakeResult("don't go.", "p", _fake_audio(2000), [
                _FakeToken("don", 0.00, 0.10, whitespace=""),
                _FakeToken("'",   0.10, 0.12, whitespace=""),
                _FakeToken("t",   0.12, 0.20, whitespace=" "),
                _FakeToken("go",  0.20, 0.40, whitespace=""),
                _FakeToken(".",   0.40, 0.42, whitespace=" "),
            ]),
        ]
        words = _extract_words(results)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0].word, "don't")
        self.assertAlmostEqual(words[0].start, 0.0)
        self.assertAlmostEqual(words[0].end, 0.20)
        self.assertEqual(words[1].word, "go.")
        self.assertAlmostEqual(words[1].start, 0.20)
        self.assertAlmostEqual(words[1].end, 0.42)

    def test_punctuation_token_with_no_timestamps_still_flushes(self):
        """A boundary-marking token without timestamps must not break grouping."""
        results = [
            _FakeResult("Hi, world", "p", _fake_audio(2000), [
                _FakeToken("Hi", 0.0, 0.2, whitespace=""),
                _FakeToken(",", None, None, whitespace=" "),
                _FakeToken("world", 0.3, 0.6, whitespace=""),
            ]),
        ]
        words = _extract_words(results)
        self.assertEqual([w.word for w in words], ["Hi", "world"])


class TestSynthesizeChapterWords(unittest.TestCase):

    @patch("openshelf.pipeline.tts.sf.write")
    def test_chunk_words_populated(self, mock_sf_write):
        """synthesize_chapter returns chunk_words with word timestamps."""
        def make_iter(text, voice=None):
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("word", 0.0, 0.3),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        result = synthesize_chapter(
            pipeline,
            _make_chunks(["text"]),
            "/tmp/out.wav",
            aligner=_FakeAligner(),
        )

        self.assertEqual(len(result.chunk_words), 1)
        self.assertEqual(len(result.chunk_words[0]), 1)
        self.assertEqual(result.chunk_words[0][0].word, "text")

    @patch("openshelf.pipeline.tts.sf.write")
    def test_chunk_words_offset_by_start(self, mock_sf_write):
        """Word timestamps in later chunks are offset by chunk_audio_starts."""
        def make_iter(text, voice=None):
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("word", 0.0, 0.3),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        result = synthesize_chapter(
            pipeline,
            _make_chunks(["A.", "B."]),
            "/tmp/out.wav",
            aligner=_FakeAligner(),
        )

        self.assertEqual(len(result.chunk_words), 2)
        # Second chunk's word should be offset
        self.assertGreater(result.chunk_words[1][0].start, 0.0)
        self.assertAlmostEqual(
            result.chunk_words[1][0].start,
            result.chunk_audio_starts[1],
            places=3,
        )

    @patch("openshelf.pipeline.tts.sf.write")
    def test_skipped_chunk_has_empty_words(self, mock_sf_write):
        """Failed chunks get empty word lists."""
        def side_effect(text, voice=None):
            if "bad" in text:
                raise RuntimeError("TTS error")
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("word", 0.0, 0.3),
            ])])

        pipeline = MagicMock(side_effect=side_effect)
        result = synthesize_chapter(
            pipeline,
            [ChunkInfo(text="bad"), ChunkInfo(text="good")],
            "/tmp/out.wav",
            aligner=_FakeAligner(),
        )
        self.assertEqual(result.chunk_words[0], [])
        self.assertEqual(len(result.chunk_words[1]), 1)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_synthesis_annotations_are_not_spoken_or_aligned_as_reader_text(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("hello", 0.1, 0.3),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        aligner = _RecordingAligner()
        chunk = ChunkInfo(
            text="hello",
            directed_segments=[DirectedSegment(
                text="[softly] hello <break time=\"350ms\"/>",
                voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                speaker="narrator",
            )],
        )

        result = synthesize_chapter(pipeline, [chunk], "/tmp/out.wav", aligner=aligner)

        self.assertEqual(pipeline.call_args_list[0].args[0], "hello")
        self.assertEqual(aligner.texts, ["hello"])
        self.assertEqual([w.word for w in result.chunk_words[0]], ["hello"])

    @patch("openshelf.pipeline.tts.sf.write")
    def test_synthesis_sanitizer_preserves_non_cue_bracket_text(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("Keep", 0.0, 0.1),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        chunk = ChunkInfo(
            text="Keep [the note] please.",
            directed_segments=[DirectedSegment(
                text="[softly] Keep [the note] please.",
                voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                speaker="narrator",
            )],
        )

        synthesize_chapter(pipeline, [chunk], "/tmp/out.wav", aligner=_FakeAligner())

        self.assertEqual(pipeline.call_args_list[0].args[0], "Keep [the note] please.")

    @patch("openshelf.pipeline.tts.sf.write")
    def test_synthesis_sanitizer_removes_bom_before_tts(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("yes", 0.0, 0.1),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        aligner = _RecordingAligner()
        chunk = ChunkInfo(
            text="\ufeffyes",
            directed_segments=[DirectedSegment(
                text="\ufeffyes",
                voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                speaker="narrator",
                original_text="\ufeffyes",
            )],
        )

        synthesize_chapter(pipeline, [chunk], "/tmp/out.wav", aligner=aligner)

        self.assertEqual(pipeline.call_args_list[0].args[0], "yes")
        self.assertEqual(aligner.texts, ["\ufeffyes"])

    @patch("openshelf.pipeline.tts.sf.write")
    def test_punctuation_only_directed_segments_render_as_silence(self, mock_sf_write):
        def make_iter(text, voice=None):
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken(text, 0.0, 0.2),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        chunk = ChunkInfo(
            text='"Hello," she said. "World."',
            directed_segments=[
                DirectedSegment(
                    text='"Hello,"',
                    voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                    speaker="Alice",
                    original_text='"Hello,"',
                ),
                DirectedSegment(
                    text="[neutral] ...",
                    voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                    speaker="narrator",
                    pause_after_ms=100,
                    original_text="...",
                ),
                DirectedSegment(
                    text='"World."',
                    voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                    speaker="Alice",
                    original_text='"World."',
                ),
            ],
        )

        result = synthesize_chapter(pipeline, [chunk], "/tmp/out.wav", aligner=_FakeAligner())

        self.assertEqual(result.skipped_chunks, 0)
        self.assertEqual(pipeline.call_count, 2)

    @patch("openshelf.pipeline.tts.sf.write")
    def test_failed_steered_segment_retries_original_text(self, mock_sf_write):
        def make_iter(text, voice=None):
            if text == "hullo":
                raise RuntimeError("bad steering")
            return iter([_FakeResult("g", "p", _fake_audio(1000), [
                _FakeToken("hello", 0.0, 0.2),
            ])])

        pipeline = MagicMock(side_effect=make_iter)
        chunk = ChunkInfo(
            text="hello",
            directed_segments=[DirectedSegment(
                text="hullo",
                voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                speaker="narrator",
                original_text="hello",
            )],
        )

        result = synthesize_chapter(pipeline, [chunk], "/tmp/out.wav", aligner=_FakeAligner())

        self.assertEqual(result.skipped_chunks, 0)
        self.assertEqual([call.args[0] for call in pipeline.call_args_list], ["hullo", "hello"])


class TestLeadInSilence(unittest.TestCase):

    @patch("openshelf.pipeline.tts.sf.write")
    def test_chapter_audio_begins_with_silence(self, mock_sf_write):
        """The first samples of the chapter are silent (lead-in pad)."""
        # Use a chunk whose audio peak is at sample 500 so fades don't zero everything.
        audio = np.zeros(1000, dtype=np.float32)
        audio[500] = 0.5
        pipeline = MagicMock()
        pipeline.return_value = iter([("g", "p", audio)])

        synthesize_chapter(pipeline, _make_chunks(["chunk"]), "/tmp/out.wav")

        written = mock_sf_write.call_args[0][1]
        # The first LEAD_IN_SAMPLES samples should be exact silence.
        self.assertEqual(_LEAD_IN_SAMPLES, int(TTS_SAMPLE_RATE * LEAD_IN_SILENCE_MS / 1000))
        np.testing.assert_array_equal(
            written[:_LEAD_IN_SAMPLES],
            np.zeros(_LEAD_IN_SAMPLES, dtype=np.float32),
        )


class TestBoundarySilenceLimiter(unittest.TestCase):

    def test_leaves_audio_unchanged_without_limit(self):
        audio = np.asarray([0.0, 0.5, 0.0], dtype=np.float32)

        result = _limit_boundary_silence(audio, TTS_SAMPLE_RATE, None)

        np.testing.assert_array_equal(result, audio)

    def test_clamps_leading_and_trailing_padding(self):
        voiced = np.ones(100, dtype=np.float32) * 0.25
        audio = np.concatenate([
            np.zeros(1000, dtype=np.float32),
            voiced,
            np.zeros(1000, dtype=np.float32),
        ])

        result = _limit_boundary_silence(audio, TTS_SAMPLE_RATE, 25)

        self.assertEqual(len(result), 100 + 2 * int(TTS_SAMPLE_RATE * 25 / 1000))

    @patch("openshelf.pipeline.tts.sf.write")
    def test_synthesis_uses_engine_boundary_silence_limit(self, mock_sf_write):
        class FakeEngine:
            capabilities = TTSCapabilities(
                emotion_control=False,
                paralinguistic_markers=False,
                speed_control=False,
                provides_timestamps=False,
                voice_cloning=True,
            )

            def post_processing_config(self):
                return PostProcessingConfig(
                    needs_forced_alignment=True,
                    voice_transition_silence_ms=0,
                    normalize_cross_voice=False,
                    max_generated_boundary_silence_ms=25,
                )

            def synthesize(self, segment):
                audio = np.concatenate([
                    np.zeros(1000, dtype=np.float32),
                    np.ones(100, dtype=np.float32) * 0.25,
                    np.zeros(1000, dtype=np.float32),
                ])
                return type("Result", (), {
                    "audio": audio,
                    "sample_rate": TTS_SAMPLE_RATE,
                    "words": None,
                })()

        chunk = ChunkInfo(
            text="Hello.",
            directed_segments=[DirectedSegment(
                text="Hello.",
                voice=VoiceSpec(id="voice"),
                speaker="narrator",
            )],
        )

        synthesize_chapter(FakeEngine(), [chunk], "/tmp/out.wav", aligner=_FakeAligner())

        written = mock_sf_write.call_args[0][1]
        expected_chunk_len = 100 + 2 * int(TTS_SAMPLE_RATE * 25 / 1000)
        self.assertEqual(len(written), _LEAD_IN_SAMPLES + expected_chunk_len)


if __name__ == "__main__":
    unittest.main()
