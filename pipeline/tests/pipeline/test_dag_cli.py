"""Tests for file-to-file pipeline DAG commands."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import contextlib  # noqa: E402
import io  # noqa: E402

from unittest.mock import MagicMock, patch  # noqa: E402

from openshelf.pipeline.dag_cli import (  # noqa: E402
    chunk_chapters,
    collect_coverage,
    direct_chapter,
    main,
    sync_chapter,
    synth_chapter,
    upload_build,
)
from openshelf.pipeline.text_chunker import (  # noqa: E402
    Chunk,
    read_chapter_chunks_artifact,
    write_chapter_chunks_artifact,
)


def _write_sync(path: str, number: int, coverage: dict, chunks=None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "number": number,
                "audio_filename": f"chapter-{number:02d}.m4a",
                "chunk_audio_starts": [0.0],
                "coverage": coverage,
                "chunks": chunks or [],
            },
            f,
        )


class TestDagCliAssemble(unittest.TestCase):
    def test_assemble_writes_chapter_data_from_chunk_and_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks_path = os.path.join(tmp, "chapter-01.chunks.json")
            sync_path = os.path.join(tmp, "chapter-01.sync.json")
            write_chapter_chunks_artifact(
                chunks_path,
                1,
                "Chapter One",
                [Chunk("Reader text.", 0, 0, "ch1-el0000", "ch1-el0000")],
            )
            with open(sync_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "number": 1,
                        "audio_filename": "chapter-01.m4a",
                        "chunk_audio_starts": [0.0],
                        "coverage": {
                            "reader_word_count": 2,
                            "aligned_word_count": 1,
                            "coverage_ratio": 0.5,
                            "first_missing_word_offset": 1,
                            "chunks": [],
                        },
                        "chunks": [
                            {
                                "index": 0,
                                "words": [{"word": "Reader", "start": 0.0, "end": 0.4}],
                            },
                        ],
                    },
                    f,
                )

            exit_code = main([
                "assemble",
                "--build-dir",
                tmp,
                "--rendition",
                "kokoro-af-heart",
                "--build-id",
                "abc123",
            ])

            with open(os.path.join(tmp, "chapter_data.json"), "r", encoding="utf-8") as f:
                payload = json.load(f)

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rendition"], "kokoro-af-heart")
        self.assertEqual(payload["build"], "abc123")
        self.assertEqual(payload["chapters"][0]["title"], "Chapter One")
        self.assertEqual(payload["chapters"][0]["chunks"][0]["text"], "Reader text.")
        self.assertEqual(
            payload["chapters"][0]["chunks"][0]["words"],
            [{"word": "Reader", "start": 0.0, "end": 0.4}],
        )


class TestDagCliCoverage(unittest.TestCase):
    def test_collect_coverage_aggregates_book_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "chapter-01.sync.json"),
                1,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 10,
                    "coverage_ratio": 1.0,
                    "first_missing_word_offset": None,
                },
            )
            _write_sync(
                os.path.join(tmp, "chapter-02.sync.json"),
                2,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 6,
                    "coverage_ratio": 0.6,
                    "first_missing_word_offset": 6,
                },
            )

            report = collect_coverage(tmp)

        self.assertEqual(report["reader_word_count"], 20)
        self.assertEqual(report["aligned_word_count"], 16)
        self.assertEqual(report["coverage_ratio"], 0.8)
        # First gap is in chapter 2 at chapter-local offset 6, after 10 chapter-1 words.
        self.assertEqual(report["first_missing_word_offset"], 16)
        self.assertEqual([c["number"] for c in report["chapters"]], [1, 2])

    def test_coverage_command_exits_zero_on_low_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "chapter-01.sync.json"),
                1,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 0,
                    "coverage_ratio": 0.0,
                    "first_missing_word_offset": 0,
                },
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = main(["coverage", "--build-dir", tmp])

        # Diagnostic only: low coverage never fails the command.
        self.assertEqual(exit_code, 0)
        self.assertIn("0/10", buffer.getvalue())

    def test_coverage_command_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "chapter-01.sync.json"),
                1,
                {
                    "reader_word_count": 4,
                    "aligned_word_count": 4,
                    "coverage_ratio": 1.0,
                    "first_missing_word_offset": None,
                },
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = main(["coverage", "--build-dir", tmp, "--json"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["coverage_ratio"], 1.0)
        self.assertEqual(payload["chapters"][0]["number"], 1)

    def test_coverage_command_errors_when_no_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                collect_coverage(tmp)


def _book_parse_payload() -> dict:
    return {
        "version": 1,
        "epub_hash": "sha256:abc",
        "parser_version": "1",
        "metadata": {"title": "T", "author": "A", "source": "gutenberg"},
        "chapters": [
            {
                "number": 1,
                "title": "Chapter One",
                "epub_item_name": "ch1.xhtml",
                "word_count": 2,
                "elements": [
                    {"id": "ch1-el0000", "tag": "p", "html": "<p>hello world</p>",
                     "text": "hello world", "spoken": True},
                ],
            },
            {
                "number": 2,
                "title": "Chapter Two",
                "epub_item_name": "ch2.xhtml",
                "word_count": 2,
                "elements": [
                    {"id": "ch2-el0000", "tag": "p", "html": "<p>second chapter</p>",
                     "text": "second chapter", "spoken": True},
                ],
            },
        ],
    }


class TestDagCliChunk(unittest.TestCase):
    def _write_book_parse(self, tmp: str) -> str:
        path = os.path.join(tmp, "book_parse.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_book_parse_payload(), f)
        return path

    def test_chunk_writes_chapter_chunks_for_all_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            written = chunk_chapters(book_parse_path, tmp)

            self.assertEqual(len(written), 2)
            artifact = read_chapter_chunks_artifact(
                os.path.join(tmp, "chapter-01.chunks.json")
            )
            self.assertEqual(artifact["number"], 1)
            self.assertEqual(artifact["title"], "Chapter One")
            self.assertEqual(artifact["chunks"][0]["text"], "hello world")
            self.assertEqual(artifact["chunks"][0]["el_start"], "ch1-el0000")

    def test_chunk_respects_chapter_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            written = chunk_chapters(book_parse_path, tmp, selected_chapters={2})

            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].endswith("chapter-02.chunks.json"))
            self.assertFalse(
                os.path.exists(os.path.join(tmp, "chapter-01.chunks.json"))
            )

    def test_chunk_errors_on_missing_selected_chapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            with self.assertRaises(ValueError):
                chunk_chapters(book_parse_path, tmp, selected_chapters={99})

    def test_chunk_via_main_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            args = ["chunk", "--book-parse", book_parse_path, "--build-dir", tmp]
            self.assertEqual(main(args), 0)
            # Re-running with identical inputs skips without error.
            self.assertEqual(main(args), 0)


def _make_local_build(tmp: str, rendition: str, build_id: str) -> str:
    """Lay out a minimal local book + build directory for upload tests."""
    book_dir = os.path.join(tmp, "kafka", "metamorphosis")
    build_dir = os.path.join(book_dir, "audio", rendition, "builds", build_id)
    os.makedirs(build_dir)
    for name in ("chapter-01.m4a", "chapter_data.json", "rendition-manifest.json",
                 "character_registry.json", "voice_direction.json", "run.json"):
        open(os.path.join(build_dir, name), "w").close()
    open(os.path.join(book_dir, "cover.jpg"), "w").close()
    open(os.path.join(book_dir, "book-annotated.epub"), "w").close()
    with open(os.path.join(book_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "title": "Metamorphosis",
                "author": "Franz Kafka",
                "source": "gutenberg",
                "renditions": {
                    rendition: {
                        "voice": "af_heart",
                        "engine": "kokoro",
                        "display": "Heart",
                        "current_build": build_id,
                        "available_builds": [build_id],
                    }
                },
            },
            f,
        )
    return book_dir


def _no_prior_client() -> MagicMock:
    """Mock R2 client: build not yet uploaded, no prior book manifest."""
    from botocore.exceptions import ClientError

    client = MagicMock()
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject",
    )
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject",
    )
    return client


class TestDagCliUpload(unittest.TestCase):
    RENDITION = "kokoro-af-heart"
    BUILD = "2a4f9c1b3d8e7f60"

    def test_upload_publishes_build_and_book_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_dir = _make_local_build(tmp, self.RENDITION, self.BUILD)
            client = _no_prior_client()

            keys = upload_build(
                book_dir=book_dir,
                rendition=self.RENDITION,
                build_id=self.BUILD,
                bucket="openshelf",
                client=client,
            )

            # Per-build objects + book manifest were all uploaded.
            self.assertTrue(any(k.endswith("rendition-manifest.json") for k in keys))
            self.assertTrue(any(k.endswith("manifest.json") for k in keys))
            uploaded_keys = [c.args[2] for c in client.upload_file.call_args_list]
            self.assertTrue(any("chapter-01.m4a" in k for k in uploaded_keys))
            self.assertTrue(any(k.endswith("/cover.jpg") for k in uploaded_keys))

    def test_upload_errors_when_required_artifact_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_dir = _make_local_build(tmp, self.RENDITION, self.BUILD)
            os.remove(os.path.join(
                book_dir, "audio", self.RENDITION, "builds", self.BUILD,
                "chapter_data.json",
            ))
            with self.assertRaises(FileNotFoundError):
                upload_build(
                    book_dir=book_dir, rendition=self.RENDITION, build_id=self.BUILD,
                    bucket="openshelf", client=_no_prior_client(),
                )

    def test_upload_errors_when_rendition_absent_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_dir = _make_local_build(tmp, self.RENDITION, self.BUILD)
            # Lay out a second rendition's build artifacts so the required-file
            # check passes, but leave it out of manifest.json.
            other_build = os.path.join(
                book_dir, "audio", "kokoro-bf-emma", "builds", self.BUILD
            )
            os.makedirs(other_build)
            for name in ("chapter_data.json", "rendition-manifest.json"):
                open(os.path.join(other_build, name), "w").close()
            with self.assertRaises(ValueError):
                upload_build(
                    book_dir=book_dir, rendition="kokoro-bf-emma", build_id=self.BUILD,
                    bucket="openshelf", client=_no_prior_client(),
                )


class _StubDirector:
    """Stand-in for AudioDirector that produces solo narrator segments."""

    def __init__(self, engine_name="kokoro"):
        from types import SimpleNamespace

        self.engine = SimpleNamespace(name=engine_name)
        self.last_chapter_fallback_used = False
        self.last_chapter_error = None

    def direct_chapter(self, title, windows, registry):
        from openshelf.pipeline.tts_engine import DirectedSegment

        directed = [
            [DirectedSegment(
                text=w.text,
                voice=registry.narrator_voice,
                speaker="narrator",
                original_text=w.text,
            )]
            for w in windows
        ]
        return registry, directed


class TestDagCliDirect(unittest.TestCase):
    def _setup(self, tmp: str) -> str:
        build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
        os.makedirs(build_dir)
        write_chapter_chunks_artifact(
            os.path.join(build_dir, "chapter-01.chunks.json"),
            1, "Chapter One",
            [Chunk("hello world", 0, 0, "ch1-el0000", "ch1-el0000")],
        )
        with open(os.path.join(build_dir, "character_registry.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"narrator_voice": {"id": "af_heart", "preset_name": "af_heart"},
                 "characters": {}},
                f,
            )
        return build_dir

    def test_direct_writes_voice_direction_from_registry_and_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            path = direct_chapter(build_dir, 1, director=_StubDirector())

            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["rendition"], "kokoro-af-heart")
            self.assertEqual(payload["build"], "abc123")
            self.assertEqual(payload["engine"], "kokoro")
            self.assertEqual(payload["cast_mode"], "solo")
            chapter = payload["chapters"][0]
            self.assertEqual(chapter["number"], 1)
            segment = chapter["chunks"][0]["segments"][0]
            self.assertEqual(segment["speaker"], "narrator")
            self.assertEqual(segment["voice"]["preset_name"], "af_heart")

    def test_direct_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            direct_chapter(build_dir, 1, director=_StubDirector())
            # Re-running with identical inputs skips without error.
            direct_chapter(build_dir, 1, director=_StubDirector())

    def test_direct_rejects_multicast(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            with self.assertRaises(ValueError):
                direct_chapter(build_dir, 1, cast_mode="multicast", director=_StubDirector())

    def test_direct_errors_when_registry_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = os.path.join(tmp, "audio", "r", "builds", "b")
            os.makedirs(build_dir)
            write_chapter_chunks_artifact(
                os.path.join(build_dir, "chapter-01.chunks.json"),
                1, "Chapter One",
                [Chunk("hi", 0, 0, "ch1-el0000", "ch1-el0000")],
            )
            with self.assertRaises(FileNotFoundError):
                direct_chapter(build_dir, 1, director=_StubDirector())


class TestDagCliSynth(unittest.TestCase):
    def _setup(self, tmp: str) -> str:
        from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec
        from openshelf.pipeline.voice_director import (
            build_direction_chapter,
            build_voice_direction_payload,
        )

        build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
        os.makedirs(build_dir)
        write_chapter_chunks_artifact(
            os.path.join(build_dir, "chapter-01.chunks.json"),
            1, "Chapter One",
            [Chunk("hello world", 0, 0, "ch1-el0000", "ch1-el0000")],
        )
        chapter = build_direction_chapter(
            1, "Chapter One", ["hello world"],
            [[DirectedSegment(
                text="hello world",
                voice=VoiceSpec(id="af_heart", preset_name="af_heart"),
                speaker="narrator",
                original_text="hello world",
            )]],
        )
        with open(os.path.join(build_dir, "chapter-01.voice_direction.json"), "w", encoding="utf-8") as f:
            json.dump(
                build_voice_direction_payload(
                    "kokoro-af-heart", "abc123", "kokoro", "solo", [chapter]
                ),
                f,
            )
        with open(os.path.join(build_dir, "character_registry.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"narrator_voice": {"id": "af_heart", "preset_name": "af_heart"},
                 "characters": {}},
                f,
            )
        return build_dir

    def _patched(self):
        from openshelf.pipeline.tts import SynthesisResult
        from openshelf.pipeline.tts_engine import WordTimestamp

        result = SynthesisResult(
            duration_seconds=1.0,
            skipped_chunks=0,
            chunk_audio_starts=[0.0],
            chunk_words=[[WordTimestamp("hello", 0.0, 0.4), WordTimestamp("world", 0.4, 0.8)]],
        )

        def fake_encode(wav_path, m4a_path, delete_wav=True):
            open(m4a_path, "w").close()
            return 1.0

        return result, fake_encode

    def test_synth_writes_m4a_and_sync(self):
        result, fake_encode = self._patched()
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            from types import SimpleNamespace

            with patch("openshelf.pipeline.tts.synthesize_chapter", return_value=result) as mock_synth, \
                    patch("openshelf.pipeline.encoder.encode_to_aac", side_effect=fake_encode):
                path = synth_chapter(
                    build_dir, 1,
                    engine=SimpleNamespace(name="kokoro"),
                    aligner=object(),
                )

            self.assertTrue(os.path.exists(os.path.join(build_dir, "chapter-01.m4a")))
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(
                [w["word"] for w in payload["chunks"][0]["words"]],
                ["hello", "world"],
            )
            # ChunkInfo carried the directed segments from voice_direction.json.
            chunk_infos = mock_synth.call_args.args[1]
            self.assertEqual(chunk_infos[0].directed_segments[0].speaker, "narrator")
            self.assertTrue(chunk_infos[0].ends_paragraph)

    def test_synth_skips_when_outputs_exist_without_force(self):
        result, fake_encode = self._patched()
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            from types import SimpleNamespace

            with patch("openshelf.pipeline.tts.synthesize_chapter", return_value=result), \
                    patch("openshelf.pipeline.encoder.encode_to_aac", side_effect=fake_encode):
                synth_chapter(build_dir, 1, engine=SimpleNamespace(name="kokoro"), aligner=object())

            # Second run with both artifacts present and no force must not synthesize.
            with patch("openshelf.pipeline.tts.synthesize_chapter") as mock_synth, \
                    patch("openshelf.pipeline.encoder.encode_to_aac") as mock_encode:
                synth_chapter(build_dir, 1, engine=SimpleNamespace(name="kokoro"), aligner=object())
                mock_synth.assert_not_called()
                mock_encode.assert_not_called()

    def test_synth_errors_when_direction_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = os.path.join(tmp, "audio", "r", "builds", "b")
            os.makedirs(build_dir)
            write_chapter_chunks_artifact(
                os.path.join(build_dir, "chapter-01.chunks.json"),
                1, "Chapter One",
                [Chunk("hi", 0, 0, "ch1-el0000", "ch1-el0000")],
            )
            with self.assertRaises(FileNotFoundError):
                synth_chapter(build_dir, 1, engine=object(), aligner=object())


class TestDagCliSync(unittest.TestCase):
    def _setup(self, tmp: str) -> None:
        write_chapter_chunks_artifact(
            os.path.join(tmp, "chapter-01.chunks.json"),
            1, "Chapter One",
            [Chunk("hello world", 0, 0, "ch1-el0000", "ch1-el0000")],
        )
        with open(os.path.join(tmp, "chapter-01.sync.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1, "number": 1, "audio_filename": "chapter-01.m4a",
                    "chunk_audio_starts": [0.0],
                    "coverage": {}, "chunks": [{"index": 0, "words": []}],
                },
                f,
            )
        open(os.path.join(tmp, "chapter-01.m4a"), "w").close()

    @patch("openshelf.pipeline.word_aligner.align_chapter")
    def test_sync_rewrites_sync_artifact_from_alignment(self, mock_align):
        from openshelf.pipeline.word_aligner import WordEntry

        mock_align.return_value = [
            WordEntry("hello", 0.0, 0.4, 0),
            WordEntry("world", 0.4, 0.8, 0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            sync_chapter(tmp, 1, force=True)

            # The aligner received text from chunks.json and offsets from sync.json.
            args, kwargs = mock_align.call_args
            self.assertEqual(args[1], ["hello world"])  # chunk_texts
            self.assertEqual(args[2], [0.0])             # chunk_audio_starts

            with open(os.path.join(tmp, "chapter-01.sync.json"), encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(
                [w["word"] for w in payload["chunks"][0]["words"]],
                ["hello", "world"],
            )
            self.assertEqual(payload["coverage"]["aligned_word_count"], 2)
            self.assertEqual(payload["coverage"]["coverage_ratio"], 1.0)

    @patch("openshelf.pipeline.word_aligner.align_chapter")
    def test_sync_differing_output_requires_force(self, mock_align):
        from openshelf.pipeline.word_aligner import WordEntry

        mock_align.return_value = [WordEntry("hello", 0.0, 0.4, 0)]
        with tempfile.TemporaryDirectory() as tmp:
            self._setup(tmp)
            with self.assertRaises(FileExistsError):
                sync_chapter(tmp, 1, force=False)

    def test_sync_errors_when_inputs_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                sync_chapter(tmp, 1, force=True)


if __name__ == "__main__":
    unittest.main()
