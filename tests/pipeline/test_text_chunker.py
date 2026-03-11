"""Tests for text_chunker — Step 2 of the pipeline."""

import os
import sys
import unittest

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.text_chunker import chunk_text, serialize_chunks, deserialize_chunks, sha256_file
from openshelf.config import CHUNK_MAX_WORDS


def _words(n: int, base: str = "word") -> str:
    """Generate a string with exactly n words."""
    return " ".join(f"{base}{i}" for i in range(n))


def _word_count(text: str) -> int:
    return len(text.split())


class TestChunkTextBasic(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(chunk_text(""), [])

    def test_whitespace_only(self):
        self.assertEqual(chunk_text("   \n\t  "), [])

    def test_single_word(self):
        self.assertEqual(chunk_text("Hello"), ["Hello"])

    def test_short_text_single_chunk(self):
        text = _words(100)
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)

    def test_text_exactly_at_max(self):
        text = _words(CHUNK_MAX_WORDS)
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(_word_count(chunks[0]), CHUNK_MAX_WORDS)


class TestChunkTextSentenceSplitting(unittest.TestCase):

    def test_splits_on_period(self):
        s1 = _words(300) + "."
        s2 = _words(300) + "."
        chunks = chunk_text(f"{s1} {s2}")
        self.assertEqual(len(chunks), 2)

    def test_splits_on_exclamation(self):
        s1 = _words(300) + "!"
        s2 = _words(300) + "!"
        chunks = chunk_text(f"{s1} {s2}")
        self.assertEqual(len(chunks), 2)

    def test_splits_on_question_mark(self):
        s1 = _words(300) + "?"
        s2 = _words(300) + "?"
        chunks = chunk_text(f"{s1} {s2}")
        self.assertEqual(len(chunks), 2)

    def test_no_mid_sentence_split(self):
        s1 = _words(400) + "."
        s2 = _words(100) + "."
        chunks = chunk_text(f"{s1} {s2}")
        # 400-word sentence can't merge with 100-word one (400+100=500 > 450)
        # period attaches to last word, so word count stays at 400/100
        self.assertEqual(len(chunks), 2)
        self.assertEqual(_word_count(chunks[0]), 400)
        self.assertEqual(_word_count(chunks[1]), 100)

    def test_greedy_accumulation(self):
        s1 = _words(200) + "."
        s2 = _words(200) + "."
        s3 = _words(200) + "."
        chunks = chunk_text(f"{s1} {s2} {s3}")
        # 200+200=400 fits, then 200 alone
        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(_word_count(chunks[0]), CHUNK_MAX_WORDS)


class TestChunkTextPunctuationEdgeCases(unittest.TestCase):

    def test_ellipsis(self):
        text = "He paused... Then continued."
        chunks = chunk_text(text)
        self.assertTrue(len(chunks) >= 1)
        self.assertTrue(all(c.strip() for c in chunks))

    def test_multiple_punctuation(self):
        text = "Really?! You think so?!"
        chunks = chunk_text(text)
        self.assertTrue(len(chunks) >= 1)

    def test_text_ending_without_punctuation(self):
        text = "This has no ending period"
        chunks = chunk_text(text)
        self.assertEqual(chunks, ["This has no ending period"])

    def test_quoted_dialogue(self):
        text = '"Hello there." She smiled. "How are you?"'
        chunks = chunk_text(text)
        self.assertTrue(len(chunks) >= 1)
        # all words preserved
        all_words = " ".join(chunks).split()
        self.assertEqual(len(all_words), len(text.split()))

    def test_newlines_between_paragraphs(self):
        text = "First paragraph.\n\nSecond paragraph."
        chunks = chunk_text(text)
        self.assertTrue(len(chunks) >= 1)


class TestChunkTextAbbreviations(unittest.TestCase):

    def test_dr_not_split(self):
        text = "Dr. Smith went to the store. He bought milk."
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        self.assertIn("Dr.", joined)

    def test_mr_mrs_not_split(self):
        text = "Mr. and Mrs. Jones arrived. They were late."
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        self.assertIn("Mr.", joined)
        self.assertIn("Mrs.", joined)

    def test_etc_not_split(self):
        text = "Apples, oranges, etc. were on the table. She picked one."
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        self.assertIn("etc.", joined)

    def test_single_letter_initial(self):
        text = "J. K. Rowling wrote many books. They were popular."
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        self.assertIn("J.", joined)
        self.assertIn("K.", joined)

    def test_military_abbreviations(self):
        text = "Col. Mustard met Capt. Plum and Lt. Green."
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        self.assertIn("Col.", joined)
        self.assertIn("Capt.", joined)
        self.assertIn("Lt.", joined)

    def test_geographic_abbreviations(self):
        text = "Mt. Everest and Ft. Knox are famous. They attract visitors."
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        self.assertIn("Mt.", joined)
        self.assertIn("Ft.", joined)


class TestChunkTextOversizedSentences(unittest.TestCase):

    def test_over_max_splits_at_commas(self):
        # Build a 600-word sentence with commas every 100 words
        parts = [_words(100) for _ in range(6)]
        sentence = ", ".join(parts) + "."
        chunks = chunk_text(sentence)
        self.assertTrue(len(chunks) >= 2)
        for c in chunks:
            self.assertLessEqual(_word_count(c), CHUNK_MAX_WORDS)

    def test_no_commas_splits_at_words(self):
        sentence = _words(500) + "."
        chunks = chunk_text(sentence)
        self.assertTrue(len(chunks) >= 2)
        for c in chunks:
            self.assertLessEqual(_word_count(c), CHUNK_MAX_WORDS)

    def test_mix_normal_and_oversized(self):
        normal = _words(200) + "."
        oversized = _words(600) + "."
        text = f"{normal} {oversized}"
        chunks = chunk_text(text)
        self.assertTrue(len(chunks) >= 2)
        for c in chunks:
            self.assertLessEqual(_word_count(c), CHUNK_MAX_WORDS)

    def test_sentence_exactly_at_max_no_split(self):
        sentence = _words(CHUNK_MAX_WORDS) + "."
        chunks = chunk_text(sentence)
        # 450 words + "." attached to last word = still 450 words
        self.assertEqual(len(chunks), 1)

    def test_sentence_at_451_words_no_commas(self):
        sentence = _words(451) + "."
        chunks = chunk_text(sentence)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(_word_count(chunks[0]), CHUNK_MAX_WORDS)
        self.assertEqual(_word_count(chunks[1]), 1)  # last word with period attached


class TestChunkTextParagraphAwareness(unittest.TestCase):

    def test_paragraph_boundaries_preserved_in_chunks(self):
        paras = [_words(100) + "." for _ in range(10)]
        text = "\n\n".join(paras)
        chunks = chunk_text(text)
        # Paragraphs packed into same chunk should retain \n\n separator
        # 4 x 100-word paragraphs fit in 450 max, so at least one chunk has \n\n
        multi_para_chunks = [c for c in chunks if "\n\n" in c]
        self.assertTrue(len(multi_para_chunks) >= 1)
        # No chunk exceeds max words
        for c in chunks:
            self.assertLessEqual(_word_count(c), CHUNK_MAX_WORDS)

    def test_short_paragraphs_packed(self):
        p1 = _words(100) + "."
        p2 = _words(100) + "."
        text = f"{p1}\n\n{p2}"
        chunks = chunk_text(text)
        # 100 + 100 = 200, fits in one chunk
        self.assertEqual(len(chunks), 1)

    def test_oversized_paragraph_split_at_sentences(self):
        sentences = [_words(100) + "." for _ in range(6)]
        para = " ".join(sentences)  # 600 words, one paragraph
        chunks = chunk_text(para)
        self.assertTrue(len(chunks) >= 2)
        for c in chunks:
            self.assertLessEqual(_word_count(c), CHUNK_MAX_WORDS)

    def test_paragraph_boundary_respected(self):
        p1 = _words(300) + "."
        p2 = _words(300) + "."
        text = f"{p1}\n\n{p2}"
        chunks = chunk_text(text)
        # 300 + 300 = 600 > max, but even if they fit, paragraph boundary
        # means they're separate units. Since 300+300 > 450, must be 2 chunks.
        self.assertEqual(len(chunks), 2)

    def test_dialogue_paragraphs_packed(self):
        lines = [
            '"Yes," he said.',
            '"No," she replied.',
            '"Maybe," they agreed.',
            '"Fine," he conceded.',
            '"Good," she nodded.',
        ]
        text = "\n\n".join(lines)
        chunks = chunk_text(text)
        # All very short, should pack into 1 chunk
        self.assertEqual(len(chunks), 1)
        total_words = sum(_word_count(line) for line in lines)
        self.assertEqual(_word_count(chunks[0]), total_words)


class TestChunkTextInvariants(unittest.TestCase):

    def test_no_chunk_exceeds_max(self):
        paras = [_words(200) + "." for _ in range(20)]
        text = "\n\n".join(paras)
        chunks = chunk_text(text)
        for c in chunks:
            self.assertLessEqual(_word_count(c), CHUNK_MAX_WORDS)

    def test_all_words_preserved(self):
        text = "The quick brown fox.\n\nJumped over the lazy dog."
        chunks = chunk_text(text)
        original_words = text.split()
        chunk_words = " ".join(chunks).split()
        self.assertEqual(sorted(original_words), sorted(chunk_words))

    def test_order_preserved(self):
        sentences = [f"Sentence{i} " + _words(50) + "." for i in range(10)]
        text = "\n\n".join(sentences)
        chunks = chunk_text(text)
        rejoined = " ".join(chunks)
        for i in range(10):
            self.assertIn(f"Sentence{i}", rejoined)
        # Check order
        positions = [rejoined.index(f"Sentence{i}") for i in range(10)]
        self.assertEqual(positions, sorted(positions))


class TestSerializeChunks(unittest.TestCase):

    def _sample_chapters(self):
        return [
            {"number": 1, "title": "The Arrest", "chunks": ["chunk one.", "chunk two."]},
            {"number": 2, "title": "First Hearing", "chunks": ["chunk three."]},
        ]

    def test_roundtrip(self):
        chapters = self._sample_chapters()
        json_str = serialize_chunks(chapters, "abc123")
        result = deserialize_chunks(json_str)
        self.assertEqual(result["chapters"], chapters)

    def test_contains_version(self):
        json_str = serialize_chunks(self._sample_chapters(), "abc123")
        result = deserialize_chunks(json_str)
        self.assertEqual(result["version"], 1)

    def test_contains_epub_sha256(self):
        json_str = serialize_chunks(self._sample_chapters(), "deadbeef")
        result = deserialize_chunks(json_str)
        self.assertEqual(result["source_epub_sha256"], "deadbeef")

    def test_preserves_unicode(self):
        chapters = [{"number": 1, "title": "Über", "chunks": ["Ça va bien."]}]
        json_str = serialize_chunks(chapters, "abc")
        result = deserialize_chunks(json_str)
        self.assertEqual(result["chapters"][0]["title"], "Über")
        self.assertEqual(result["chapters"][0]["chunks"][0], "Ça va bien.")

    def test_empty_chapters(self):
        json_str = serialize_chunks([], "abc")
        result = deserialize_chunks(json_str)
        self.assertEqual(result["chapters"], [])


class TestSha256File(unittest.TestCase):

    def test_computes_hash(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            path = f.name
        try:
            result = sha256_file(path)
            import hashlib
            expected = hashlib.sha256(b"hello world").hexdigest()
            self.assertEqual(result, expected)
        finally:
            os.unlink(path)

    def test_empty_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            result = sha256_file(path)
            import hashlib
            expected = hashlib.sha256(b"").hexdigest()
            self.assertEqual(result, expected)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
