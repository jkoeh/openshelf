"""Tests for file-to-file pipeline DAG commands."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import contextlib  # noqa: E402
import io  # noqa: E402

from unittest.mock import MagicMock, patch  # noqa: E402

from openshelf.pipeline.dag.cli import (  # noqa: E402
    build_registry,
    chunk_chapters,
    collect_coverage,
    direct_chapter,
    main,
    repair_pauses_chapter,
    run_book,
    sync_chapter,
    synth_chapter,
    upload_build,
    _direction_cache_hit_message,
    _load_or_copy_chapter_direction,
    _resolve_tts_device,
)
from openshelf.pipeline.text_chunker import (  # noqa: E402
    Chunk,
    read_section_chunks_artifact,
    write_section_chunks_artifact,
)


def _write_sync(path: str, number: int, coverage: dict, chunks=None) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 2,
                "sequence": number,
                "audio_filename": f"section-{number:02d}.m4a",
                "heading_words": [],
                "chunk_audio_starts": [0.0],
                "coverage": coverage,
                "chunks": chunks or [],
            },
            f,
        )


class TestDagCliAssemble(unittest.TestCase):
    def test_assemble_writes_section_data_from_heading_chunk_and_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks_path = os.path.join(tmp, "section-01.chunks.json")
            sync_path = os.path.join(tmp, "section-01.sync.json")
            heading = {
                "display_label": "I",
                "display_title": "Chapter One",
                "spoken_text": "Chapter One.",
                "element_ids": ["sec1-el0000"],
            }
            write_section_chunks_artifact(
                chunks_path,
                1,
                "chapter",
                1,
                heading,
                [Chunk("Reader text.", 0, 0, "sec1-el0001", "sec1-el0001")],
            )
            with open(sync_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "sequence": 1,
                        "audio_filename": "section-01.m4a",
                        "heading_words": [
                            {"word": "Chapter", "start": 0.0, "end": 0.3},
                        ],
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

            with open(os.path.join(tmp, "section_data.json"), "r", encoding="utf-8") as f:
                payload = json.load(f)

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rendition"], "kokoro-af-heart")
        self.assertEqual(payload["build"], "abc123")
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["sections"][0]["heading"]["display_label"], "I")
        self.assertEqual(
            payload["sections"][0]["heading"]["words"],
            [{"word": "Chapter", "start": 0.0, "end": 0.3}],
        )
        self.assertEqual(payload["sections"][0]["chunks"][0]["text"], "Reader text.")
        self.assertEqual(
            payload["sections"][0]["chunks"][0]["words"],
            [{"word": "Reader", "start": 0.0, "end": 0.4}],
        )


class TestDagCliCoverage(unittest.TestCase):
    def test_collect_coverage_aggregates_book_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "section-01.sync.json"),
                1,
                {
                    "reader_word_count": 10,
                    "aligned_word_count": 10,
                    "coverage_ratio": 1.0,
                    "first_missing_word_offset": None,
                },
            )
            _write_sync(
                os.path.join(tmp, "section-02.sync.json"),
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
        self.assertEqual([c["number"] for c in report["sections"]], [1, 2])

    def test_coverage_command_exits_zero_on_low_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_sync(
                os.path.join(tmp, "section-01.sync.json"),
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
                os.path.join(tmp, "section-01.sync.json"),
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
        self.assertEqual(payload["sections"][0]["number"], 1)

    def test_coverage_command_errors_when_no_sync_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                collect_coverage(tmp)


def _book_parse_payload() -> dict:
    return {
        "version": 2,
        "epub_hash": "sha256:abc",
        "parser_version": "2",
        "metadata": {
            "title": "T",
            "author": "A",
            "source": "gutenberg",
            "language": "en",
        },
        "sections": [
            {
                "sequence": 1,
                "section_type": "chapter",
                "ordinal": 1,
                "heading": {
                    "display_label": "I",
                    "display_title": "Chapter One",
                    "spoken_text": "Chapter One.",
                    "element_ids": ["sec1-el0000"],
                },
                "epub_item_name": "ch1.xhtml",
                "word_count": 2,
                "elements": [
                    {"id": "sec1-el0000", "tag": "h2", "html": "<h2>I</h2>",
                     "text": "I", "spoken": True},
                    {"id": "sec1-el0001", "tag": "p", "html": "<p>hello world</p>",
                     "text": "hello world", "spoken": True},
                ],
            },
            {
                "sequence": 2,
                "section_type": "epilogue",
                "ordinal": None,
                "heading": {
                    "display_label": "Epilogue",
                    "display_title": "",
                    "spoken_text": "Epilogue.",
                    "element_ids": ["sec2-el0000"],
                },
                "epub_item_name": "ch2.xhtml",
                "word_count": 2,
                "elements": [
                    {"id": "sec2-el0000", "tag": "h2", "html": "<h2>Epilogue</h2>",
                     "text": "Epilogue", "spoken": True},
                    {"id": "sec2-el0001", "tag": "p", "html": "<p>second chapter</p>",
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

    def test_chunk_writes_credits_and_source_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            written = chunk_chapters(book_parse_path, tmp)

            self.assertEqual(len(written), 4)
            artifact = read_section_chunks_artifact(
                os.path.join(tmp, "section-02.chunks.json")
            )
            self.assertEqual(artifact["sequence"], 2)
            self.assertEqual(artifact["section_type"], "chapter")
            self.assertEqual(artifact["ordinal"], 1)
            self.assertEqual(artifact["heading"]["display_label"], "I")
            self.assertEqual(artifact["chunks"][0]["text"], "hello world")
            self.assertEqual(artifact["chunks"][0]["el_start"], "sec1-el0001")

    def test_chunk_respects_chapter_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            written = chunk_chapters(book_parse_path, tmp, selected_chapters={3})

            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].endswith("section-03.chunks.json"))
            self.assertFalse(
                os.path.exists(os.path.join(tmp, "section-02.chunks.json"))
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

    def test_chunk_generates_deterministic_opening_and_closing_credits(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            build_dir = os.path.join(
                tmp,
                "audio",
                "kokoro-af-heart",
                "builds",
                "1234567890abcdef",
            )
            os.makedirs(build_dir)

            chunk_chapters(book_parse_path, build_dir)

            opening = read_section_chunks_artifact(
                os.path.join(build_dir, "section-01.chunks.json")
            )
            closing = read_section_chunks_artifact(
                os.path.join(build_dir, "section-04.chunks.json")
            )

        self.assertEqual(opening["section_type"], "opening_credits")
        self.assertEqual(
            opening["heading"]["spoken_text"],
            "T. Written by A. Narrated by OpenShelf using the Heart voice.",
        )
        self.assertEqual(closing["section_type"], "closing_credits")
        self.assertEqual(
            closing["heading"]["spoken_text"],
            "The end of T, written by A, narrated by OpenShelf using the Heart voice.",
        )


def _make_local_build(tmp: str, rendition: str, build_id: str) -> str:
    """Lay out a minimal local book + build directory for upload tests."""
    book_dir = os.path.join(tmp, "kafka", "metamorphosis")
    build_dir = os.path.join(book_dir, "audio", rendition, "builds", build_id)
    os.makedirs(build_dir)
    for name in ("section-01.m4a", "section_data.json", "rendition-manifest.json",
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
            self.assertTrue(any("section-01.m4a" in k for k in uploaded_keys))
            self.assertTrue(any(k.endswith("/cover.jpg") for k in uploaded_keys))

    def test_upload_errors_when_required_artifact_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_dir = _make_local_build(tmp, self.RENDITION, self.BUILD)
            os.remove(os.path.join(
                book_dir, "audio", self.RENDITION, "builds", self.BUILD,
                "section_data.json",
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
            for name in ("section_data.json", "rendition-manifest.json"):
                open(os.path.join(other_build, name), "w").close()
            with self.assertRaises(ValueError):
                upload_build(
                    book_dir=book_dir, rendition="kokoro-bf-emma", build_id=self.BUILD,
                    bucket="openshelf", client=_no_prior_client(),
                )

    def test_upload_publishes_v2_pointer_before_deleting_v1_builds(self):
        old_build = "1111111111111111"
        prior = {
            "title": "Metamorphosis",
            "author": "Franz Kafka",
            "source": "gutenberg",
            "renditions": {
                self.RENDITION: {
                    "voice": "af_heart",
                    "engine": "kokoro",
                    "display": "Heart",
                    "current_build": old_build,
                    "available_builds": [old_build],
                },
            },
        }
        events: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            book_dir = _make_local_build(tmp, self.RENDITION, self.BUILD)
            client = MagicMock()
            client.get_object.return_value = {
                "Body": io.BytesIO(b'{"version": 1, "chapters": []}'),
            }

            with patch(
                "openshelf.pipeline.r2.upload_cover",
                return_value="cover",
            ), patch(
                "openshelf.pipeline.r2.upload_epub",
                return_value="epub",
            ), patch(
                "openshelf.pipeline.r2.upload_rendition_build",
                return_value=["new-build"],
            ), patch(
                "openshelf.pipeline.r2.fetch_prior_book_manifest",
                return_value=prior,
            ), patch(
                "openshelf.pipeline.r2.upload_book_manifest",
                side_effect=lambda *args, **kwargs: events.append("publish") or "manifest",
            ), patch(
                "openshelf.pipeline.r2.delete_build_prefixes",
                side_effect=lambda *args, **kwargs: events.append("delete") or ["old-object"],
            ) as delete_builds:
                upload_build(
                    book_dir=book_dir,
                    rendition=self.RENDITION,
                    build_id=self.BUILD,
                    bucket="openshelf",
                    client=client,
                )

            with open(os.path.join(book_dir, "manifest.json"), encoding="utf-8") as f:
                published = json.load(f)

        self.assertEqual(events, ["publish", "delete"])
        self.assertEqual(
            published["renditions"][self.RENDITION]["available_builds"],
            [self.BUILD],
        )
        delete_builds.assert_called_once()
        self.assertEqual(delete_builds.call_args.args[-1], [old_build])


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

    def build_registry(self, ctx, narrator_voice_override=None):
        from openshelf.pipeline.tts_engine import VoiceSpec
        from openshelf.pipeline.voice_director import CharacterRegistry

        return CharacterRegistry(
            narrator_voice=narrator_voice_override
            or VoiceSpec(id="af_heart", preset_name="af_heart"),
            characters={},
        )


class TestDagCliDirect(unittest.TestCase):
    def _setup(self, tmp: str) -> str:
        build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
        os.makedirs(build_dir)
        write_section_chunks_artifact(
            os.path.join(build_dir, "section-01.chunks.json"),
            1, "chapter", 1,
            {
                "display_label": "I",
                "display_title": "Chapter One",
                "spoken_text": "Chapter One.",
                "element_ids": ["sec1-el0000"],
            },
            [Chunk("hello world", 0, 0, "sec1-el0001", "sec1-el0001")],
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
            chapter = payload["sections"][0]
            self.assertEqual(chapter["sequence"], 1)
            segment = chapter["chunks"][0]["segments"][0]
            self.assertEqual(segment["speaker"], "narrator")
            self.assertEqual(segment["voice"]["preset_name"], "af_heart")

    def test_direct_via_main_accepts_performance_direction_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            with patch("openshelf.pipeline.dag.cli.direct_chapter") as mocked:
                mocked.return_value = os.path.join(build_dir, "section-01.voice_direction.json")
                exit_code = main([
                    "direct",
                    "--build-dir",
                    build_dir,
                    "--chapter",
                    "1",
                    "--performance-direction",
                    "off",
                ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(mocked.call_args.kwargs["performance_direction_mode"], "off")

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
            write_section_chunks_artifact(
                os.path.join(build_dir, "section-01.chunks.json"),
                1, "chapter", 1,
                {
                    "display_label": "I",
                    "display_title": "Chapter One",
                    "spoken_text": "Chapter One.",
                    "element_ids": [],
                },
                [Chunk("hi", 0, 0, "sec1-el0001", "sec1-el0001")],
            )
            with self.assertRaises(FileNotFoundError):
                direct_chapter(build_dir, 1, director=_StubDirector())


class TestDagCliDirectionCache(unittest.TestCase):
    def _write_direction(
        self,
        build_dir: str,
        *,
        rendition: str,
        build_id: str,
        engine: str,
        text: str = "hello world",
    ) -> str:
        from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec
        from openshelf.pipeline.voice_director import (
            build_direction_chapter,
            build_voice_direction_payload,
        )

        os.makedirs(build_dir, exist_ok=True)
        chapter = build_direction_chapter(
            1,
            "Chapter One",
            [text],
            [[
                DirectedSegment(
                    text=text,
                    voice=VoiceSpec(
                        id="chatterbox-librivox-old",
                        ref_audio_path="old.wav",
                        ref_text="Old reference.",
                    ),
                    speaker="narrator",
                    original_text=text,
                    emotion="anxious",
                    engine_controls={
                        "intensity": 0.7,
                        "exaggeration": 0.696,
                        "cfg_weight": 0.402,
                    },
                )
            ]],
        )
        path = os.path.join(build_dir, "section-01.voice_direction.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                build_voice_direction_payload(
                    rendition,
                    build_id,
                    engine,
                    "solo",
                    [chapter],
                ),
                f,
            )
        return path

    def test_reuses_same_engine_chapter_direction_and_remaps_narrator_voice(self):
        from openshelf.pipeline.tts_engine import VoiceSpec

        with tempfile.TemporaryDirectory() as tmp:
            book_dir = os.path.join(tmp, "charlotte-bronte", "jane-eyre")
            old_build = os.path.join(
                book_dir,
                "audio",
                "chatterbox-librivox-old",
                "builds",
                "aaaaaaaaaaaaaaaa",
            )
            self._write_direction(
                old_build,
                rendition="chatterbox-librivox-old",
                build_id="aaaaaaaaaaaaaaaa",
                engine="chatterbox",
            )
            new_build = os.path.join(
                book_dir,
                "audio",
                "chatterbox-af-heart",
                "builds",
                "bbbbbbbbbbbbbbbb",
            )

            result = _load_or_copy_chapter_direction(
                book_dir=book_dir,
                build_dir=new_build,
                chapter_number=1,
                engine_name="chatterbox",
                cast_mode="solo",
                chunk_texts=["hello world"],
                rendition="chatterbox-af-heart",
                build_id="bbbbbbbbbbbbbbbb",
                narrator_voice=VoiceSpec(
                    id="chatterbox-af_heart",
                    ref_audio_path="af_heart.wav",
                    ref_text="Heart reference.",
                ),
            )

            self.assertIsNotNone(result)
            payload, source = result
            copied_path = os.path.join(new_build, "section-01.voice_direction.json")
            self.assertTrue(os.path.exists(copied_path))
            self.assertTrue(source.endswith("section-01.voice_direction.json"))
            self.assertEqual(payload["rendition"], "chatterbox-af-heart")
            self.assertEqual(payload["build"], "bbbbbbbbbbbbbbbb")
            segment = payload["sections"][0]["chunks"][0]["segments"][0]
            self.assertEqual(segment["emotion"], "anxious")
            self.assertEqual(segment["engine_controls"]["intensity"], 0.7)
            self.assertEqual(segment["voice"]["id"], "chatterbox-af_heart")
            self.assertEqual(segment["voice"]["ref_audio_path"], "af_heart.wav")

            with open(copied_path, encoding="utf-8") as f:
                copied = json.load(f)
            self.assertEqual(copied, payload)

    def test_direction_cache_rejects_mismatched_chunk_text(self):
        from openshelf.pipeline.tts_engine import VoiceSpec

        with tempfile.TemporaryDirectory() as tmp:
            book_dir = os.path.join(tmp, "charlotte-bronte", "jane-eyre")
            old_build = os.path.join(
                book_dir,
                "audio",
                "chatterbox-af-heart",
                "builds",
                "aaaaaaaaaaaaaaaa",
            )
            self._write_direction(
                old_build,
                rendition="chatterbox-af-heart",
                build_id="aaaaaaaaaaaaaaaa",
                engine="chatterbox",
                text="old text",
            )
            new_build = os.path.join(
                book_dir,
                "audio",
                "chatterbox-af-heart",
                "builds",
                "bbbbbbbbbbbbbbbb",
            )

            result = _load_or_copy_chapter_direction(
                book_dir=book_dir,
                build_dir=new_build,
                chapter_number=1,
                engine_name="chatterbox",
                cast_mode="solo",
                chunk_texts=["new text"],
                rendition="chatterbox-af-heart",
                build_id="bbbbbbbbbbbbbbbb",
                narrator_voice=VoiceSpec(id="chatterbox-af_heart"),
            )

            self.assertIsNone(result)
            self.assertFalse(os.path.exists(
                os.path.join(new_build, "section-01.voice_direction.json")
            ))

    def test_direction_cache_hit_message_includes_copy_source_and_target(self):
        source = os.path.join("old", "section-01.voice_direction.json")
        target = os.path.join("new", "section-01.voice_direction.json")

        self.assertEqual(
            _direction_cache_hit_message(source, target),
            f"voice direction cache hit: copied from {source} -> {target}",
        )

    def test_direction_cache_hit_message_identifies_existing_target(self):
        target = os.path.join("build", "section-01.voice_direction.json")

        self.assertEqual(
            _direction_cache_hit_message(target, target),
            f"voice direction cache hit: existing {target}",
        )


class TestDagCliRegistry(unittest.TestCase):
    def _write_book_parse(self, tmp: str) -> str:
        path = os.path.join(tmp, "book_parse.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_book_parse_payload(), f)
        return path

    def test_registry_writes_character_registry_from_book_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
            path = build_registry(book_parse_path, build_dir, director=_StubDirector())

            with open(path, encoding="utf-8") as f:
                payload = json.load(f)

        self.assertEqual(payload["narrator_voice"]["preset_name"], "af_heart")
        self.assertEqual(payload["characters"], {})

    def test_registry_via_main_forwards_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            book_parse_path = self._write_book_parse(tmp)
            build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
            with patch("openshelf.pipeline.dag.cli.build_registry") as mocked:
                mocked.return_value = os.path.join(build_dir, "character_registry.json")
                exit_code = main([
                    "registry",
                    "--book-parse",
                    book_parse_path,
                    "--build-dir",
                    build_dir,
                    "--engine",
                    "kokoro",
                    "--voice",
                    "af_heart",
                ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(mocked.call_args.kwargs["voice"], "af_heart")


class TestDagCliRun(unittest.TestCase):
    def test_run_book_dry_run_parses_and_skips_engine(self):
        from openshelf.pipeline.epub_parser import ContentElement, Section, SectionHeading
        from openshelf.pipeline.logging_utils import close_pipeline_logging

        body = ContentElement(
            id="sec1-el0001",
            tag="p",
            html="<p>Hello world</p>",
            text="Hello world",
            spoken=True,
        )
        chapter = Section(
            sequence=1,
            section_type="chapter",
            ordinal=1,
            heading=SectionHeading(
                display_label="I",
                display_title="Chapter One",
                spoken_text="Chapter One.",
                element_ids=["sec1-el0000"],
            ),
            elements=[body],
            body_elements=[body],
            paragraphs=["Hello world"],
            text="Hello world",
            word_count=2,
            epub_item_name="chapter.xhtml",
        )
        args = types.SimpleNamespace(
            epub="",
            output="audio",
            source="local",
            engine="kokoro",
            voice=None,
            cast_mode="solo",
            performance_direction="off",
            rendition=None,
            device=None,
            chapters="1",
            dry_run=True,
            keep_wav=False,
            upload=False,
            log_dir="logs",
            build_id="1234567890abcdef",
            resume=False,
            force=False,
        )

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            open(epub_path, "wb").close()
            args.epub = epub_path
            args.output = tmp
            args.log_dir = tmp

            with patch("openshelf.pipeline.dag.cli.parse_epub", return_value=[chapter]), \
                    patch(
                        "openshelf.pipeline.dag.cli.read_book_metadata",
                        return_value={"title": "Book", "author": "Author"},
                    ), \
                    patch("openshelf.pipeline.engines.create_engine") as create_engine:
                try:
                    result = run_book(args)
                finally:
                    close_pipeline_logging()

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["sections"], [1])
        create_engine.assert_not_called()

    def test_run_via_main_forwards_full_book_flags(self):
        with patch("openshelf.pipeline.dag.cli.run_book") as mocked:
            mocked.return_value = {"build_id": "abc123"}
            exit_code = main([
                "run",
                "--epub",
                "book.epub",
                "--source",
                "standard-ebooks",
                "--output",
                "audio",
                "--engine",
                "chatterbox",
                "--voice",
                "chatterbox-bf_emma",
                "--rendition",
                "chatterbox-bf-emma",
                "--cast-mode",
                "solo",
                "--performance-direction",
                "batched",
                "--device",
                "cuda",
                "--chapters",
                "1",
                "--upload",
                "--new-voice-direction",
                "--force",
            ])

        self.assertEqual(exit_code, 0)
        args = mocked.call_args.args[0]
        self.assertEqual(args.epub, "book.epub")
        self.assertEqual(args.source, "standard-ebooks")
        self.assertEqual(args.engine, "chatterbox")
        self.assertEqual(args.voice, "chatterbox-bf_emma")
        self.assertEqual(args.performance_direction, "batched")
        self.assertEqual(args.device, "cuda")
        self.assertEqual(args.chapters, "1")
        self.assertTrue(args.upload)
        self.assertTrue(args.new_voice_direction)
        self.assertTrue(args.force)

    def test_run_book_passes_selected_device_to_engine_factory(self):
        from openshelf.pipeline.epub_parser import ContentElement, Section, SectionHeading
        from openshelf.pipeline.logging_utils import close_pipeline_logging

        body = ContentElement(
            id="sec1-el0001",
            tag="p",
            html="<p>Hello world</p>",
            text="Hello world",
            spoken=True,
        )
        chapter = Section(
            sequence=1,
            section_type="chapter",
            ordinal=1,
            heading=SectionHeading(
                display_label="I",
                display_title="Chapter One",
                spoken_text="Chapter One.",
                element_ids=["sec1-el0000"],
            ),
            elements=[body],
            body_elements=[body],
            paragraphs=["Hello world"],
            text="Hello world",
            word_count=2,
            epub_item_name="chapter.xhtml",
        )
        args = types.SimpleNamespace(
            epub="",
            output="audio",
            source="local",
            engine="chatterbox",
            voice=None,
            cast_mode="solo",
            performance_direction="off",
            rendition=None,
            device="cuda",
            chapters="1",
            dry_run=False,
            keep_wav=False,
            upload=False,
            log_dir="logs",
            build_id="1234567890abcdef",
            resume=False,
            force=False,
        )

        with tempfile.TemporaryDirectory() as tmp:
            epub_path = os.path.join(tmp, "book.epub")
            open(epub_path, "wb").close()
            args.epub = epub_path
            args.output = tmp
            args.log_dir = tmp

            fake_engine = types.SimpleNamespace(name="chatterbox")
            with patch("openshelf.pipeline.dag.cli.parse_epub", return_value=[chapter]), \
                    patch(
                        "openshelf.pipeline.dag.cli.read_book_metadata",
                        return_value={"title": "Book", "author": "Author"},
                    ), \
                    patch(
                        "openshelf.pipeline.engines.create_engine",
                        return_value=fake_engine,
                    ) as create_engine, \
                    patch(
                        "openshelf.pipeline.engines.create_aligner",
                        side_effect=RuntimeError("stop after engine creation"),
                    ):
                try:
                    with self.assertRaisesRegex(RuntimeError, "stop after engine creation"):
                        run_book(args)
                finally:
                    close_pipeline_logging()

        create_engine.assert_called_once_with("chatterbox", device="cuda")

    def test_auto_chatterbox_cpu_resolution_fails_fast(self):
        with patch("openshelf.pipeline.tts.get_device", return_value="cpu"):
            with self.assertRaisesRegex(ValueError, "auto device resolved to CPU"):
                _resolve_tts_device("chatterbox", None)

    def test_explicit_chatterbox_cpu_is_allowed(self):
        self.assertEqual(_resolve_tts_device("chatterbox", "cpu"), "cpu")


class TestDagCliSynth(unittest.TestCase):
    def _setup(self, tmp: str) -> str:
        from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec
        from openshelf.pipeline.voice_director import (
            build_direction_chapter,
            build_voice_direction_payload,
        )

        build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
        os.makedirs(build_dir)
        heading = {
            "display_label": "I",
            "display_title": "Chapter One",
            "spoken_text": "Chapter One.",
            "element_ids": ["sec1-el0000"],
        }
        write_section_chunks_artifact(
            os.path.join(build_dir, "section-01.chunks.json"),
            1, "chapter", 1, heading,
            [Chunk("hello world", 0, 0, "sec1-el0001", "sec1-el0001")],
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
        with open(os.path.join(build_dir, "section-01.voice_direction.json"), "w", encoding="utf-8") as f:
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
            synthesis_units=[],
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

            self.assertTrue(os.path.exists(os.path.join(build_dir, "section-01.m4a")))
            self.assertTrue(os.path.exists(os.path.join(build_dir, "section-01.synthesis_units.json")))
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
            narrator_voice = mock_synth.call_args.kwargs["narrator_voice"]
            self.assertEqual(narrator_voice.id, "af_heart")
            self.assertEqual(narrator_voice.preset_name, "af_heart")

    def test_synth_skips_when_outputs_exist_without_force(self):
        result, fake_encode = self._patched()
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            from types import SimpleNamespace

            with patch("openshelf.pipeline.tts.synthesize_chapter", return_value=result), \
                    patch("openshelf.pipeline.encoder.encode_to_aac", side_effect=fake_encode):
                synth_chapter(build_dir, 1, engine=SimpleNamespace(name="kokoro"), aligner=object())

            # Second run with all synthesis artifacts present and no force must not synthesize.
            with patch("openshelf.pipeline.tts.synthesize_chapter") as mock_synth, \
                    patch("openshelf.pipeline.encoder.encode_to_aac") as mock_encode:
                synth_chapter(build_dir, 1, engine=SimpleNamespace(name="kokoro"), aligner=object())
                mock_synth.assert_not_called()
                mock_encode.assert_not_called()

    def test_synth_passes_device_to_engine_factory(self):
        result, fake_encode = self._patched()
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            fake_engine = types.SimpleNamespace(name="chatterbox")
            fake_aligner = object()

            with patch(
                "openshelf.pipeline.engines.create_engine",
                return_value=fake_engine,
            ) as create_engine, \
                    patch(
                        "openshelf.pipeline.engines.create_aligner",
                        return_value=fake_aligner,
                    ) as create_aligner, \
                    patch("openshelf.pipeline.tts.synthesize_chapter", return_value=result), \
                    patch("openshelf.pipeline.encoder.encode_to_aac", side_effect=fake_encode):
                synth_chapter(build_dir, 1, engine_name="chatterbox", device="cuda")

        create_engine.assert_called_once_with("chatterbox", device="cuda")
        create_aligner.assert_called_once_with(fake_engine, device="cuda")

    def test_synth_auto_device_resolves_before_engine_factory(self):
        result, fake_encode = self._patched()
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            fake_engine = types.SimpleNamespace(name="chatterbox")
            fake_aligner = object()

            with patch(
                "openshelf.pipeline.tts.get_device",
                return_value="cuda",
            ), patch(
                "openshelf.pipeline.engines.create_engine",
                return_value=fake_engine,
            ) as create_engine, patch(
                "openshelf.pipeline.engines.create_aligner",
                return_value=fake_aligner,
            ) as create_aligner, patch(
                "openshelf.pipeline.tts.synthesize_chapter",
                return_value=result,
            ), patch(
                "openshelf.pipeline.encoder.encode_to_aac",
                side_effect=fake_encode,
            ):
                synth_chapter(build_dir, 1, engine_name="chatterbox")

        create_engine.assert_called_once_with("chatterbox", device="cuda")
        create_aligner.assert_called_once_with(fake_engine, device="cuda")

    def test_synth_errors_when_direction_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = os.path.join(tmp, "audio", "r", "builds", "b")
            os.makedirs(build_dir)
            write_section_chunks_artifact(
                os.path.join(build_dir, "section-01.chunks.json"),
                1, "chapter", 1,
                {
                    "display_label": "I",
                    "display_title": "Chapter One",
                    "spoken_text": "Chapter One.",
                    "element_ids": [],
                },
                [Chunk("hi", 0, 0, "sec1-el0001", "sec1-el0001")],
            )
            with self.assertRaises(FileNotFoundError):
                synth_chapter(build_dir, 1, engine=object(), aligner=object())

    def test_reference_voice_engine_requires_complete_registry_narrator(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            os.remove(os.path.join(build_dir, "character_registry.json"))

            with self.assertRaisesRegex(FileNotFoundError, "complete narrator_voice VoiceSpec"):
                synth_chapter(
                    build_dir,
                    1,
                    engine=types.SimpleNamespace(name="chatterbox"),
                    aligner=object(),
                )


class TestDagCliRepairPauses(unittest.TestCase):

    def _setup(self, tmp: str) -> str:
        build_dir = os.path.join(tmp, "audio", "kokoro-af-heart", "builds", "abc123")
        os.makedirs(build_dir)
        heading = {
            "display_label": "I",
            "display_title": "Chapter One",
            "spoken_text": "Chapter One.",
            "element_ids": ["sec1-el0000"],
        }
        write_section_chunks_artifact(
            os.path.join(build_dir, "section-01.chunks.json"),
            1, "chapter", 1, heading,
            [Chunk("hello world", 0, 0, "sec1-el0001", "sec1-el0001")],
        )
        _write_sync(
            os.path.join(build_dir, "section-01.sync.json"),
            1,
            {"reader_word_count": 2, "aligned_word_count": 2, "coverage_ratio": 1.0},
            chunks=[
                {
                    "index": 0,
                    "words": [{"word": "hello", "start": 0.0, "end": 0.4}],
                },
            ],
        )
        with open(os.path.join(build_dir, "section-01.synthesis_units.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 2, "sequence": 1, "chunks": []}, f)
        open(os.path.join(build_dir, "section-01.m4a"), "w").close()
        with open(os.path.join(build_dir, "section_data.json"), "w", encoding="utf-8") as f:
            json.dump({"stale": True}, f)
        with open(os.path.join(build_dir, "rendition-manifest.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "build": "abc123",
                    "rendition": "kokoro-af-heart",
                    "voice": "af_heart",
                    "engine": "kokoro",
                    "pipeline_version": "test",
                    "total_duration_seconds": 3.0,
                    "version": 2,
                    "section_count": 2,
                    "sections": [
                        {
                            "sequence": 1,
                            "section_type": "chapter",
                            "ordinal": 1,
                            "display_label": "I",
                            "display_title": "Chapter One",
                            "filename": "section-01.m4a",
                            "duration_seconds": 1.0,
                            "word_count": 2,
                        },
                        {
                            "sequence": 2,
                            "section_type": "epilogue",
                            "ordinal": None,
                            "display_label": "Epilogue",
                            "display_title": "",
                            "filename": "section-02.m4a",
                            "duration_seconds": 2.0,
                            "word_count": 4,
                        },
                    ],
                },
                f,
            )
        return build_dir

    def test_repair_refreshes_existing_aggregate_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)

            with patch(
                "openshelf.pipeline.seams.repair_chapter_pause_files",
                return_value=[object()],
            ) as mock_repair, patch(
                "openshelf.pipeline.encoder.audio_duration",
                return_value=1.25,
            ):
                path = repair_pauses_chapter(build_dir, 1, force=True)

            self.assertEqual(path, os.path.join(build_dir, "section-01.sync.json"))
            mock_repair.assert_called_once()
            self.assertEqual(mock_repair.call_args.kwargs["chunk_texts"], ["hello world"])

            with open(os.path.join(build_dir, "section_data.json"), encoding="utf-8") as f:
                section_data = json.load(f)
            self.assertEqual(section_data["rendition"], "kokoro-af-heart")
            self.assertEqual(section_data["build"], "abc123")
            self.assertEqual(
                section_data["sections"][0]["chunks"][0]["words"][0]["word"],
                "hello",
            )

            with open(os.path.join(build_dir, "rendition-manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["sections"][0]["duration_seconds"], 1.25)
            self.assertEqual(manifest["total_duration_seconds"], 3.25)

    def test_repair_requires_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = self._setup(tmp)
            with self.assertRaises(ValueError):
                repair_pauses_chapter(build_dir, 1, force=False)


class TestDagCliSync(unittest.TestCase):
    def _setup(self, tmp: str) -> None:
        write_section_chunks_artifact(
            os.path.join(tmp, "section-01.chunks.json"),
            1, "chapter", 1,
            {
                "display_label": "I",
                "display_title": "Chapter One",
                "spoken_text": "Chapter One.",
                "element_ids": ["sec1-el0000"],
            },
            [Chunk("hello world", 0, 0, "sec1-el0001", "sec1-el0001")],
        )
        with open(os.path.join(tmp, "section-01.sync.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2, "sequence": 1, "audio_filename": "section-01.m4a",
                    "heading_words": [],
                    "chunk_audio_starts": [0.0],
                    "coverage": {}, "chunks": [{"index": 0, "words": []}],
                },
                f,
            )
        open(os.path.join(tmp, "section-01.m4a"), "w").close()

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

            with open(os.path.join(tmp, "section-01.sync.json"), encoding="utf-8") as f:
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
