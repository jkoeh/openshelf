"""Tests for text_chunker — Step 2 of the pipeline."""

import os
import sys
import tempfile
import unittest

# Allow running without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.text_chunker import (
    Chunk,
    build_section_chunks_artifact,
    chunk_text,
    read_section_chunks_artifact,
    write_section_chunks_artifact,
)
from openshelf.config import CHUNK_MAX_WORDS


def _words(n: int, base: str = "word") -> str:
    """Generate a string with exactly n words."""
    return " ".join(f"{base}{i}" for i in range(n))


def _word_count(text: str) -> int:
    return len(text.split())


class TestChunkTextBasic(unittest.TestCase):

    def test_empty_list(self):
        self.assertEqual(chunk_text([]), [])

    def test_whitespace_only_paragraph(self):
        self.assertEqual(chunk_text(["   \n\t  "]), [])

    def test_single_word(self):
        result = chunk_text(["Hello"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "Hello")

    def test_short_text_single_chunk(self):
        result = chunk_text([_words(100)])
        self.assertEqual(len(result), 1)

    def test_text_exactly_at_max(self):
        result = chunk_text([_words(CHUNK_MAX_WORDS)])
        self.assertEqual(len(result), 1)
        self.assertEqual(_word_count(result[0].text), CHUNK_MAX_WORDS)

    def test_returns_chunk_objects(self):
        result = chunk_text(["hello world"])
        self.assertIsInstance(result[0], Chunk)


class TestChunkTextSentenceSplitting(unittest.TestCase):

    def test_splits_on_period(self):
        s1 = _words(150) + "."
        s2 = _words(150) + "."
        result = chunk_text([f"{s1} {s2}"])
        self.assertEqual(len(result), 2)

    def test_splits_on_exclamation(self):
        s1 = _words(150) + "!"
        s2 = _words(150) + "!"
        result = chunk_text([f"{s1} {s2}"])
        self.assertEqual(len(result), 2)

    def test_splits_on_question_mark(self):
        s1 = _words(150) + "?"
        s2 = _words(150) + "?"
        result = chunk_text([f"{s1} {s2}"])
        self.assertEqual(len(result), 2)

    def test_no_mid_sentence_split(self):
        s1 = _words(CHUNK_MAX_WORDS) + "."
        s2 = _words(100) + "."
        result = chunk_text([f"{s1} {s2}"])
        self.assertEqual(len(result), 2)
        self.assertEqual(_word_count(result[0].text), CHUNK_MAX_WORDS)
        self.assertEqual(_word_count(result[1].text), 100)

    def test_greedy_accumulation(self):
        s1 = _words(100) + "."
        s2 = _words(100) + "."
        s3 = _words(100) + "."
        result = chunk_text([f"{s1} {s2} {s3}"])
        self.assertEqual(len(result), 2)
        self.assertLessEqual(_word_count(result[0].text), CHUNK_MAX_WORDS)


class TestChunkTextPunctuationEdgeCases(unittest.TestCase):

    def test_ellipsis(self):
        result = chunk_text(["He paused... Then continued."])
        self.assertTrue(len(result) >= 1)
        self.assertTrue(all(c.text.strip() for c in result))

    def test_multiple_punctuation(self):
        result = chunk_text(["Really?! You think so?!"])
        self.assertTrue(len(result) >= 1)

    def test_text_ending_without_punctuation(self):
        result = chunk_text(["This has no ending period"])
        self.assertEqual(result[0].text, "This has no ending period")

    def test_quoted_dialogue(self):
        text = '"Hello there." She smiled. "How are you?"'
        result = chunk_text([text])
        self.assertTrue(len(result) >= 1)
        all_words = " ".join(c.text for c in result).split()
        self.assertEqual(len(all_words), len(text.split()))

    def test_two_paragraphs(self):
        result = chunk_text(["First paragraph.", "Second paragraph."])
        self.assertTrue(len(result) >= 1)


class TestChunkTextAbbreviations(unittest.TestCase):

    def test_dr_not_split(self):
        result = chunk_text(["Dr. Smith went to the store. He bought milk."])
        joined = " ".join(c.text for c in result)
        self.assertIn("Dr.", joined)

    def test_mr_mrs_not_split(self):
        result = chunk_text(["Mr. and Mrs. Jones arrived. They were late."])
        joined = " ".join(c.text for c in result)
        self.assertIn("Mr.", joined)
        self.assertIn("Mrs.", joined)

    def test_etc_not_split(self):
        result = chunk_text(["Apples, oranges, etc. were on the table. She picked one."])
        joined = " ".join(c.text for c in result)
        self.assertIn("etc.", joined)

    def test_single_letter_initial(self):
        result = chunk_text(["J. K. Rowling wrote many books. They were popular."])
        joined = " ".join(c.text for c in result)
        self.assertIn("J.", joined)
        self.assertIn("K.", joined)

    def test_military_abbreviations(self):
        result = chunk_text(["Col. Mustard met Capt. Plum and Lt. Green."])
        joined = " ".join(c.text for c in result)
        self.assertIn("Col.", joined)
        self.assertIn("Capt.", joined)
        self.assertIn("Lt.", joined)

    def test_geographic_abbreviations(self):
        result = chunk_text(["Mt. Everest and Ft. Knox are famous. They attract visitors."])
        joined = " ".join(c.text for c in result)
        self.assertIn("Mt.", joined)
        self.assertIn("Ft.", joined)


class TestChunkTextOversizedSentences(unittest.TestCase):

    def test_over_max_splits_at_commas(self):
        parts = [_words(50) for _ in range(6)]
        sentence = ", ".join(parts) + "."
        result = chunk_text([sentence])
        self.assertTrue(len(result) >= 2)
        for c in result:
            self.assertLessEqual(_word_count(c.text), CHUNK_MAX_WORDS)

    def test_no_commas_splits_at_words(self):
        result = chunk_text([_words(CHUNK_MAX_WORDS + 50) + "."])
        self.assertTrue(len(result) >= 2)
        for c in result:
            self.assertLessEqual(_word_count(c.text), CHUNK_MAX_WORDS)

    def test_mix_normal_and_oversized(self):
        normal = _words(100) + "."
        oversized = _words(CHUNK_MAX_WORDS + 100) + "."
        result = chunk_text([f"{normal} {oversized}"])
        self.assertTrue(len(result) >= 2)
        for c in result:
            self.assertLessEqual(_word_count(c.text), CHUNK_MAX_WORDS)

    def test_sentence_exactly_at_max_no_split(self):
        result = chunk_text([_words(CHUNK_MAX_WORDS) + "."])
        self.assertEqual(len(result), 1)

    def test_sentence_one_over_max_splits(self):
        result = chunk_text([_words(CHUNK_MAX_WORDS + 1) + "."])
        self.assertEqual(len(result), 2)
        self.assertEqual(_word_count(result[0].text), CHUNK_MAX_WORDS)
        self.assertEqual(_word_count(result[1].text), 1)


class TestChunkTextParagraphAwareness(unittest.TestCase):

    def test_paragraph_boundaries_preserved_in_chunks(self):
        paras = [_words(100) + "." for _ in range(10)]
        result = chunk_text(paras)
        multi_para_chunks = [c for c in result if "\n\n" in c.text]
        self.assertTrue(len(multi_para_chunks) >= 1)
        for c in result:
            self.assertLessEqual(_word_count(c.text), CHUNK_MAX_WORDS)

    def test_short_paragraphs_packed(self):
        result = chunk_text([_words(80) + ".", _words(80) + "."])
        # 80 + 80 = 160, fits in one chunk (max=200)
        self.assertEqual(len(result), 1)

    def test_oversized_paragraph_split_at_sentences(self):
        sentences = [_words(80) + "." for _ in range(4)]
        para = " ".join(sentences)  # 320 words, one paragraph
        result = chunk_text([para])
        self.assertTrue(len(result) >= 2)
        for c in result:
            self.assertLessEqual(_word_count(c.text), CHUNK_MAX_WORDS)

    def test_large_paragraphs_not_packed(self):
        result = chunk_text([_words(150) + ".", _words(150) + "."])
        # 150 + 150 = 300 > max(200), so must be 2 chunks
        self.assertEqual(len(result), 2)

    def test_dialogue_paragraphs_packed(self):
        lines = [
            '"Yes," he said.',
            '"No," she replied.',
            '"Maybe," they agreed.',
            '"Fine," he conceded.',
            '"Good," she nodded.',
        ]
        result = chunk_text(lines)
        self.assertEqual(len(result), 1)
        total_words = sum(_word_count(line) for line in lines)
        self.assertEqual(_word_count(result[0].text), total_words)


class TestChunkTextInvariants(unittest.TestCase):

    def test_no_chunk_exceeds_max(self):
        paras = [_words(200) + "." for _ in range(20)]
        result = chunk_text(paras)
        for c in result:
            self.assertLessEqual(_word_count(c.text), CHUNK_MAX_WORDS)

    def test_all_words_preserved(self):
        paras = ["The quick brown fox.", "Jumped over the lazy dog."]
        result = chunk_text(paras)
        original_words = " ".join(paras).split()
        chunk_words = " ".join(c.text for c in result).split()
        self.assertEqual(sorted(original_words), sorted(chunk_words))

    def test_order_preserved(self):
        sentences = [f"Sentence{i} " + _words(50) + "." for i in range(10)]
        result = chunk_text(sentences)
        rejoined = " ".join(c.text for c in result)
        positions = [rejoined.index(f"Sentence{i}") for i in range(10)]
        self.assertEqual(positions, sorted(positions))


class TestChunkTextParaIndices(unittest.TestCase):

    def test_single_para_indices_zero(self):
        result = chunk_text([_words(50)])
        self.assertEqual(result[0].para_start, 0)
        self.assertEqual(result[0].para_end, 0)

    def test_two_short_paras_packed_same_chunk(self):
        result = chunk_text([_words(100), _words(100)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].para_start, 0)
        self.assertEqual(result[0].para_end, 1)

    def test_two_large_paras_separate_chunks(self):
        result = chunk_text([_words(150), _words(150)])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].para_start, 0)
        self.assertEqual(result[0].para_end, 0)
        self.assertEqual(result[1].para_start, 1)
        self.assertEqual(result[1].para_end, 1)

    def test_oversized_para_splits_keep_same_index(self):
        sentences = [_words(80) + "." for _ in range(4)]
        para = " ".join(sentences)  # 320 words, splits into 2+ chunks
        result = chunk_text([para])
        self.assertTrue(len(result) >= 2)
        for c in result:
            self.assertEqual(c.para_start, 0)
            self.assertEqual(c.para_end, 0)

    def test_three_paragraphs_mixed_packing(self):
        # para 0: 180 words (alone — 180+30>200), para 1+2: 30 words each (packed together)
        result = chunk_text([_words(180), _words(30), _words(30)])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].para_start, 0)
        self.assertEqual(result[0].para_end, 0)
        self.assertEqual(result[1].para_start, 1)
        self.assertEqual(result[1].para_end, 2)

    def test_para_end_gte_para_start(self):
        paras = [_words(50) for _ in range(10)]
        result = chunk_text(paras)
        for c in result:
            self.assertGreaterEqual(c.para_end, c.para_start)


class TestChunkTextElementIds(unittest.TestCase):

    def test_element_ids_propagated_to_el_start_el_end(self):
        paras = [_words(50), _words(50), _words(50)]
        ids = ["ch1-el0000", "ch1-el0001", "ch1-el0002"]
        result = chunk_text(paras, element_ids=ids)
        # all three pack into one chunk (150 < 200); el_start = first id, el_end = last id
        self.assertEqual(result[0].el_start, "ch1-el0000")
        self.assertEqual(result[0].el_end, "ch1-el0002")

    def test_element_ids_two_chunks(self):
        paras = [_words(150), _words(150)]
        ids = ["ch1-el0000", "ch1-el0001"]
        result = chunk_text(paras, element_ids=ids)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].el_start, "ch1-el0000")
        self.assertEqual(result[0].el_end, "ch1-el0000")
        self.assertEqual(result[1].el_start, "ch1-el0001")
        self.assertEqual(result[1].el_end, "ch1-el0001")

    def test_no_element_ids_defaults_to_empty(self):
        result = chunk_text([_words(50)])
        self.assertEqual(result[0].el_start, "")
        self.assertEqual(result[0].el_end, "")

    def test_element_ids_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            chunk_text([_words(50), _words(50)], element_ids=["ch1-el0000"])


class TestSectionChunksArtifact(unittest.TestCase):

    def test_builds_indexed_chunk_artifact_with_text_hashes(self):
        chunks = [
            Chunk("First paragraph.", 0, 0, "ch1-el0000", "ch1-el0000"),
            Chunk("Second paragraph.", 1, 1, "ch1-el0001", "ch1-el0001"),
        ]

        heading = {
            "display_label": "I",
            "display_title": "Chapter One",
            "spoken_text": "Chapter One.",
            "element_ids": ["sec1-el0000"],
        }
        artifact = build_section_chunks_artifact(
            1,
            "chapter",
            1,
            heading,
            chunks,
        )

        self.assertEqual(
            artifact,
            {
                "version": 2,
                "sequence": 1,
                "section_type": "chapter",
                "ordinal": 1,
                "heading": heading,
                "chunks": [
                    {
                        "index": 0,
                        "text": "First paragraph.",
                        "para_start": 0,
                        "para_end": 0,
                        "el_start": "ch1-el0000",
                        "el_end": "ch1-el0000",
                        "text_hash": (
                            "sha256:"
                            "98ea01bc109a52fdf7145c10c648e8b27b8ebc877aaa79405f20b044ecfcacaa"
                        ),
                    },
                    {
                        "index": 1,
                        "text": "Second paragraph.",
                        "para_start": 1,
                        "para_end": 1,
                        "el_start": "ch1-el0001",
                        "el_end": "ch1-el0001",
                        "text_hash": (
                            "sha256:"
                            "3fc3deaa2b3609eb7d096478e4e522fc071348be1b8e116ce204cff63c08af80"
                        ),
                    },
                ],
            },
        )

    def test_write_is_idempotent_and_rejects_different_existing_payload(self):
        chunks = [Chunk("First paragraph.", 0, 0)]
        changed_chunks = [Chunk("Changed paragraph.", 0, 0)]

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "section-01.chunks.json")
            heading = {
                "display_label": "I",
                "display_title": "",
                "spoken_text": "Chapter One.",
                "element_ids": ["sec1-el0000"],
            }

            write_section_chunks_artifact(path, 1, "chapter", 1, heading, chunks)
            write_section_chunks_artifact(path, 1, "chapter", 1, heading, chunks)
            persisted = read_section_chunks_artifact(path)

            self.assertEqual(persisted["chunks"][0]["text"], "First paragraph.")
            with self.assertRaises(FileExistsError):
                write_section_chunks_artifact(
                    path,
                    1,
                    "chapter",
                    1,
                    heading,
                    changed_chunks,
                )


if __name__ == "__main__":
    unittest.main()
