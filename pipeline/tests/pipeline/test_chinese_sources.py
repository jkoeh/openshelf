"""Offline source-to-reading-copy checks and Mandarin model wiring."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from openshelf.pipeline import job_consumer, source_catalog, wikisource_source as ws
from openshelf.pipeline.epub_parser import parse_epub
from openshelf.pipeline.language import normalize_language, unit_count
from openshelf.pipeline.text_chunker import chunk_text


def edition():
    return {"language": "zh", "title": "古书", "author": "古人", "publication_year": 1800,
            "root": {"title": "古书", "page_id": 1, "revision": 10},
            "chapters": [{"title": "古书/第十回", "page_id": 2, "revision": 11},
                         {"title": "古书/第二回", "page_id": 3, "revision": 12}]}


def fetcher(language, page):
    html = ('<div class="mw-parser-output"><div class="ws-summary"><a href="/wiki/古书/第十回">十</a>'
            '<a href="/wiki/古书/第二回">二</a></div></div>') if page["page_id"] == 1 else (
                '<div class="mw-parser-output"><div class="header">wiki chrome</div><p>天地玄黃，宇宙洪荒。你好世界！</p></div>')
    return {"title": page["title"], "pageid": page["page_id"], "revid": page["revision"],
            "text": html, "templates": [{"title": "Template:PD-1923"}]}


class ChineseSourcesTests(unittest.TestCase):
    def test_snapshot_preserves_explicit_order_identity_rights_and_script(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "book.epub"
            book = ws.build_edition(edition(), path, fetcher=fetcher)
            source_catalog.validate_reading_copy(path, book)
            sections = parse_epub(str(path))
            self.assertEqual([section.ordinal for section in sections], [10, 2])
            self.assertEqual([section.sequence for section in sections], [1, 2])
            self.assertTrue(all(section.word_count == 12 for section in sections))
            self.assertEqual(sections[0].paragraphs, ["天地玄黃，宇宙洪荒。你好世界！"])
            self.assertNotIn("wiki chrome", sections[0].text)
            self.assertNotIn("contributors", sections[0].text)
            changed = edition()
            changed["chapters"][1]["revision"] = 99
            other = ws.build_edition(changed, None, fetcher=fetcher)
            self.assertNotEqual(book["source_id"], other["source_id"])
            with self.assertRaises(job_consumer.RightsNotVerified):
                job_consumer.check_epub_rights(path, other["source_id"])

    def test_ambiguous_rights_toc_revision_and_translation_fail_closed(self):
        def local_pd(language, page):
            result = fetcher(language, page)
            result["templates"] = [{"title": "Template:PD-China"}]
            return result
        with self.assertRaisesRegex(ValueError, "U.S."):
            ws.build_edition(edition(), None, fetcher=local_pd)
        wrong_order = edition()
        wrong_order["chapters"].reverse()
        with self.assertRaisesRegex(ValueError, "order"):
            ws.build_edition(wrong_order, None, fetcher=fetcher)
        late = edition()
        late["publication_year"] = 2000
        with self.assertRaisesRegex(ValueError, "translation"):
            ws.build_edition(late, None, fetcher=fetcher)
        def wrong_revision(language, page):
            result = fetcher(language, page)
            result["revid"] += 1
            return result
        with self.assertRaisesRegex(ValueError, "identity"):
            ws.build_edition(edition(), None, fetcher=wrong_revision)

    def test_chinese_chunking_bounds_preserves_punctuation_and_element_ids(self):
        text = "天地玄黃，宇宙洪荒。" * 45 + "OpenShelf 2026，你好！"
        chunks = chunk_text([text], max_words=20, language="zh-Hant", element_ids=["p1"])
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(unit_count(chunk.text) <= 20 for chunk in chunks))
        self.assertEqual("".join(chunk.text for chunk in chunks).replace("\n\n", ""), text)
        self.assertTrue(all(chunk.el_start == chunk.el_end == "p1" for chunk in chunks))
        self.assertEqual(normalize_language("zh-Hant-TW"), "zh")

    def test_mandarin_factory_and_language_mismatch(self):
        from openshelf.pipeline.engines import create_aligner, create_engine
        from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec
        engine = create_engine("kokoro", language="zh-Hant")
        self.assertEqual(len(engine.available_voices()), 8)
        self.assertTrue(all(voice.id.startswith("z") for voice in engine.available_voices()))
        self.assertEqual(create_aligner(engine, language="zh").language, "zh")
        with self.assertRaisesRegex(ValueError, "English"):
            create_engine("chatterbox", language="zh")
        with self.assertRaisesRegex(ValueError, "voice"):
            engine.synthesize(DirectedSegment(text="你好", voice=VoiceSpec("af_heart"), speaker="narrator"))

    def test_alignment_uses_chinese_model_sentence_weights_and_real_timings(self):
        from openshelf.pipeline.word_aligner import align_chapter, sync_coverage_metrics
        from openshelf.pipeline.tts_engine import WordTimestamp
        import numpy as np
        fake = SimpleNamespace(load_audio=lambda _: np.zeros(16000 * 6),
                               load_align_model=lambda **kwargs: ("model", kwargs),
                               align=lambda segments, model, metadata, audio, device: {"word_segments": [
                                   {"word": "天", "start": .1, "end": .3}, {"word": "地", "start": .3, "end": .6},
                                   {"word": "，"}, {"word": "玄", "start": 2.1, "end": 2.4}]})
        with patch.dict(sys.modules, {"whisperx": fake}), patch.object(fake, "load_align_model", wraps=fake.load_align_model) as load, \
                patch.object(fake, "align", wraps=fake.align) as align:
            words = align_chapter("unused.wav", ["天地。玄黃宇宙。"], [0.0], language="zh-Hant")
        load.assert_called_once_with(language_code="zh", device="cpu")
        self.assertEqual([segment["end"] for segment in align.call_args.args[0]], [2.0, 6.0])
        self.assertEqual([word.word for word in words], ["天", "地", "玄"])
        self.assertEqual([word.start for word in words], [.1, .3, 2.1])
        self.assertEqual(sync_coverage_metrics([[WordTimestamp("天", .1, .3)]], ["天地。"])["reader_word_count"], 2)

    def test_mandarin_runtime_and_localized_credits(self):
        from openshelf.pipeline import tts
        from openshelf.pipeline.engines import create_engine
        from openshelf.pipeline.dag.cli import _credit_headings
        with patch.object(tts, "load_pipeline", return_value="mandarin-runtime") as load:
            self.assertEqual(create_engine("kokoro", language="zh")._pipeline(), "mandarin-runtime")
        load.assert_called_once_with(device=None, language="z")
        opening, closing = _credit_headings("古书", "古人", "小贝", "zh")
        self.assertIn("朗读", opening["spoken_text"])
        self.assertIn("朗读完毕", closing["spoken_text"])
        self.assertNotIn("Written by", opening["spoken_text"])

    def test_chinese_alignment_combines_latin_character_timings(self):
        from openshelf.pipeline.word_aligner import _chinese_text_units, WordEntry
        entries = [WordEntry("天", .1, .2, 0), WordEntry("H", .3, .4, 0),
                   WordEntry("i", .4, .5, 0), WordEntry("地", .6, .7, 0)]
        units = _chinese_text_units(entries, ["天 Hi，地。"])
        self.assertEqual([(word.word, word.start, word.end) for word in units],
                         [("天", .1, .2), ("Hi", .3, .5), ("地", .6, .7)])

    def test_gutenberg_rights_require_exact_embedded_identifier(self):
        from ebooklib import epub
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "book.epub"
            book = epub.EpubBook()
            book.set_identifier("http://www.gutenberg.org/ebooks/11")
            book.set_title("Book")
            book.set_language("en")
            book.add_metadata("DC", "rights", "Public domain in the USA.")
            chapter = epub.EpubHtml(title="Chapter", file_name="one.xhtml")
            chapter.content = "<h1>Chapter I</h1><p>Readable text.</p>"
            book.add_item(chapter)
            book.spine = [chapter]
            epub.write_epub(str(path), book)
            job_consumer.check_epub_rights(path, "gutenberg:11")
            with self.assertRaisesRegex(job_consumer.RightsNotVerified, "identifier"):
                job_consumer.check_epub_rights(path, "gutenberg:12")
            book.set_identifier("http://www.gutenberg.org/11")
            epub.write_epub(str(path), book)
            job_consumer.check_epub_rights(path, "gutenberg:11")
            with self.assertRaisesRegex(job_consumer.RightsNotVerified, "identifier"):
                job_consumer.check_epub_rights(path, "gutenberg:12")

    def test_snapshot_total_bound_stops_before_epub_construction(self):
        with patch.object(ws, "MAX_SNAPSHOT_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "snapshot exceeds"):
                ws.build_edition(edition(), None, fetcher=fetcher)

    def test_cached_mandarin_job_never_requests_upstream(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "reading.epub"
            book = ws.build_edition(edition(), path, fetcher=fetcher)
            source_catalog.validate_reading_copy(path, book)
            job = {**book, "id": "job", "lease_token": "lease", "build_id": "1234567890abcdef",
                   "epub_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            class API:
                def download_source_epub(self, source_id, destination):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(path.read_bytes())
                    return True
                def post(self, *args):
                    return {}
            class Child:
                returncode = 0
                def poll(self):
                    return 0
            with patch.object(job_consumer, "verify_public_domain") as upstream, \
                    patch.object(job_consumer.subprocess, "Popen", return_value=Child()) as launch:
                job_consumer.process_job(API(), job, download_root=root / "download", audio_root=root / "audio", device="cpu", mandarin_python="mandarin-python")

            upstream.assert_not_called()
            command = launch.call_args.args[0]
            self.assertEqual(command[0], "mandarin-python")
            self.assertEqual(command[command.index("--voice") + 1], "qwen3-serena")
            self.assertEqual(command[command.index("--rendition") + 1], "qwen3-serena")
            job["epub_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hash"):
                job_consumer.process_job(API(), job, download_root=root / "download", audio_root=root / "audio", device="cpu")

    def test_chinese_local_selectors_cannot_match_an_unrelated_edition(self):
        from openshelf.pipeline.books import find_local_epub
        with tempfile.TemporaryDirectory() as folder:
            unrelated = Path(folder) / "gutenberg" / "writer" / "other-g11.epub"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_bytes(b"unrelated")
            self.assertIsNone(find_local_epub("古人", "古书", folder))
