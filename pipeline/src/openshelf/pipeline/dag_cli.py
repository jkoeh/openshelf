"""File-to-file pipeline DAG commands."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from typing import Sequence

from openshelf.pipeline.epub_parser import (
    build_book_parse_artifact,
    epub_sha256,
    parse_epub,
    read_book_metadata,
    read_book_parse_artifact,
    write_book_parse_artifact,
)
from openshelf.pipeline.text_chunker import (
    chunk_text,
    read_chapter_chunks_artifact,
    write_chapter_chunks_artifact,
)


CHAPTER_DATA_VERSION = 1
_CHUNKS_RE = re.compile(r"chapter-(\d+)\.chunks\.json$")
_SYNC_RE = re.compile(r"chapter-(\d+)\.sync\.json$")


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json_idempotent(path: str, payload: dict, force: bool = False) -> str:
    if os.path.exists(path) and not force:
        existing = _read_json(path)
        if existing == payload:
            return path
        raise FileExistsError(f"output already exists with different payload: {path}")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def _chapter_number_from_chunks_path(path: str) -> int:
    match = _CHUNKS_RE.search(os.path.basename(path))
    if not match:
        raise ValueError(f"not a chapter chunks artifact: {path}")
    return int(match.group(1))


def _sync_path_for_chunks_path(chunks_path: str) -> str:
    return chunks_path.replace(".chunks.json", ".sync.json")


def _chapter_number_from_sync_path(path: str) -> int:
    match = _SYNC_RE.search(os.path.basename(path))
    if not match:
        raise ValueError(f"not a chapter sync artifact: {path}")
    return int(match.group(1))


def _coverage_ratio(aligned_word_count: int, reader_word_count: int) -> float:
    if reader_word_count == 0:
        return 1.0 if aligned_word_count == 0 else 0.0
    return round(aligned_word_count / reader_word_count, 4)


def collect_coverage(build_dir: str) -> dict:
    """Aggregate per-chapter coverage from chapter-NN.sync.json into a book report.

    Diagnostic only: this reads the `coverage` block already recorded in each sync
    artifact and rolls it up. It does not judge whether coverage is acceptable.
    """
    sync_paths = sorted(
        glob.glob(os.path.join(build_dir, "chapter-*.sync.json")),
        key=_chapter_number_from_sync_path,
    )
    if not sync_paths:
        raise FileNotFoundError(f"no chapter sync artifacts found in {build_dir}")

    chapters: list[dict] = []
    total_reader_words = 0
    total_aligned_words = 0
    first_missing_word_offset: int | None = None

    for sync_path in sync_paths:
        sync_payload = _read_json(sync_path)
        number = sync_payload["number"]
        coverage = sync_payload.get("coverage", {})
        reader_words = coverage.get("reader_word_count", 0)
        aligned_words = coverage.get("aligned_word_count", 0)
        chapter_missing = coverage.get("first_missing_word_offset")

        if first_missing_word_offset is None and chapter_missing is not None:
            first_missing_word_offset = total_reader_words + chapter_missing

        chapters.append({
            "number": number,
            "reader_word_count": reader_words,
            "aligned_word_count": aligned_words,
            "coverage_ratio": coverage.get(
                "coverage_ratio", _coverage_ratio(aligned_words, reader_words)
            ),
            "first_missing_word_offset": chapter_missing,
        })
        total_reader_words += reader_words
        total_aligned_words += aligned_words

    return {
        "build_dir": build_dir,
        "reader_word_count": total_reader_words,
        "aligned_word_count": total_aligned_words,
        "coverage_ratio": _coverage_ratio(total_aligned_words, total_reader_words),
        "first_missing_word_offset": first_missing_word_offset,
        "chapters": chapters,
    }


def _format_coverage_report(report: dict) -> str:
    lines = [f"Coverage report for {report['build_dir']}"]
    lines.append(
        f"  Book total: {report['aligned_word_count']}/{report['reader_word_count']} "
        f"words aligned (ratio {report['coverage_ratio']})"
    )
    if report["first_missing_word_offset"] is not None:
        lines.append(
            f"  First missing word offset: {report['first_missing_word_offset']}"
        )
    for chapter in report["chapters"]:
        missing = chapter["first_missing_word_offset"]
        missing_label = "" if missing is None else f"  first missing @ {missing}"
        lines.append(
            f"  Chapter {chapter['number']:>2}: "
            f"{chapter['aligned_word_count']}/{chapter['reader_word_count']} "
            f"(ratio {chapter['coverage_ratio']}){missing_label}"
        )
    return "\n".join(lines)


def build_chapter_data_payload(
    build_dir: str,
    rendition: str,
    build_id: str,
    selected_chapters: set[int] | None = None,
) -> dict:
    """Assemble the public chapter_data.json payload from chunk + sync artifacts.

    Shared by the `assemble` DAG command and the convert-book orchestrator.
    ``selected_chapters`` restricts assembly to a subset (e.g. a local
    --chapters sample run); None assembles every chapter present.
    """
    chunks_paths = sorted(
        glob.glob(os.path.join(build_dir, "chapter-*.chunks.json")),
        key=_chapter_number_from_chunks_path,
    )
    if not chunks_paths:
        raise FileNotFoundError(f"no chapter chunks artifacts found in {build_dir}")

    payload: dict = {
        "version": CHAPTER_DATA_VERSION,
        "rendition": rendition,
        "build": build_id,
        "chapters": [],
    }

    for chunks_path in chunks_paths:
        number = _chapter_number_from_chunks_path(chunks_path)
        if selected_chapters is not None and number not in selected_chapters:
            continue
        chunk_payload = read_chapter_chunks_artifact(chunks_path)
        sync_path = _sync_path_for_chunks_path(chunks_path)
        if not os.path.exists(sync_path):
            raise FileNotFoundError(f"missing sync artifact for {chunks_path}: {sync_path}")
        sync_payload = _read_json(sync_path)
        sync_chunks = {
            int(chunk["index"]): chunk.get("words", [])
            for chunk in sync_payload.get("chunks", [])
        }
        chunks = chunk_payload.get("chunks", [])
        entry = {
            "number": chunk_payload["number"],
            "title": chunk_payload["title"],
            "word_count": sum(len(chunk["text"].split()) for chunk in chunks),
            "chunks": [],
        }
        for chunk in chunks:
            index = int(chunk["index"])
            entry["chunks"].append({
                "text": chunk["text"],
                "words": sync_chunks.get(index, []),
            })
        payload["chapters"].append(entry)

    return payload


def assemble_chapter_data(
    build_dir: str,
    rendition: str,
    build_id: str,
    output_path: str | None = None,
    selected_chapters: set[int] | None = None,
    force: bool = False,
) -> str:
    payload = build_chapter_data_payload(
        build_dir, rendition, build_id, selected_chapters=selected_chapters
    )
    destination = output_path or os.path.join(build_dir, "chapter_data.json")
    return _write_json_idempotent(destination, payload, force=force)


def _parse_chapter_filter(value: str | None) -> set[int] | None:
    """Parse a chapter number/range filter like ``2`` or ``2,4-5`` into a set."""
    if not value:
        return None
    selected: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"invalid chapter range: {part}")
            selected.update(range(start, end + 1))
        else:
            number = int(part)
            if number <= 0:
                raise ValueError(f"invalid chapter number: {part}")
            selected.add(number)
    return selected


def parse_book(
    epub_path: str,
    output_path: str,
    source: str = "",
    force: bool = False,
) -> str:
    """Parse an EPUB into the durable ``book_parse.json`` artifact."""
    chapters = parse_epub(epub_path)
    if not chapters:
        raise ValueError(f"no chapters found in EPUB: {epub_path}")
    metadata = read_book_metadata(epub_path)
    metadata["source"] = source
    payload = build_book_parse_artifact(chapters, epub_sha256(epub_path), metadata)
    return write_book_parse_artifact(output_path, payload, force=force)


def chunk_chapters(
    book_parse_path: str,
    build_dir: str,
    selected_chapters: set[int] | None = None,
    force: bool = False,
) -> list[str]:
    """Write ``chapter-NN.chunks.json`` for each selected chapter in book_parse."""
    book_parse = read_book_parse_artifact(book_parse_path)
    written: list[str] = []
    for chapter in book_parse.get("chapters", []):
        number = chapter["number"]
        if selected_chapters is not None and number not in selected_chapters:
            continue
        elements = chapter.get("elements", [])
        paragraphs = [el["text"] for el in elements if el["spoken"]]
        element_ids = [el["id"] for el in elements if el["spoken"]]
        chunks = chunk_text(paragraphs, element_ids=element_ids)
        path = os.path.join(build_dir, f"chapter-{number:02d}.chunks.json")
        write_chapter_chunks_artifact(
            path, number, chapter["title"], chunks, force=force
        )
        written.append(path)
    if selected_chapters is not None:
        found = {
            ch["number"]
            for ch in book_parse.get("chapters", [])
            if ch["number"] in selected_chapters
        }
        missing = sorted(selected_chapters - found)
        if missing:
            raise ValueError(
                f"--chapters selected missing chapter(s): "
                f"{', '.join(str(n) for n in missing)}"
            )
    return written


def _rendition_build_from_build_dir(build_dir: str) -> tuple[str, str]:
    """Derive (rendition, build) from an ``audio/{rendition}/builds/{build}`` path."""
    build_dir = os.path.normpath(build_dir)
    build_id = os.path.basename(build_dir)
    rendition = os.path.basename(os.path.dirname(os.path.dirname(build_dir)))
    return rendition, build_id


def direct_chapter(
    build_dir: str,
    chapter_number: int,
    engine_name: str | None = None,
    cast_mode: str = "solo",
    device: str | None = None,
    force: bool = False,
    director=None,
) -> str:
    """Run LLM voice direction for one chapter and write chapter-NN.voice_direction.json.

    Reads chapter-NN.chunks.json (reader text) and character_registry.json
    (narrator voice). Solo cast mode only — multicast registry repair is out of
    scope (see plans/pipeline-resumable-repair-plan.md). This is the only repair
    stage that may call the LLM; heavy engine/LLM deps import lazily.
    """
    from openshelf.pipeline.voice_director import (
        AudioDirector,
        CharacterRegistry,
        build_chunk_windows,
        build_direction_chapter,
        build_voice_direction_payload,
        voice_spec_from_dict,
    )

    if cast_mode != "solo":
        raise ValueError("direct repair supports solo cast mode only")

    chunks_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.chunks.json")
    registry_path = os.path.join(build_dir, "character_registry.json")
    direction_path = os.path.join(
        build_dir, f"chapter-{chapter_number:02d}.voice_direction.json"
    )
    for required in (chunks_path, registry_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for direct: {required}")

    chunk_payload = read_chapter_chunks_artifact(chunks_path)
    title = chunk_payload["title"]
    chunks = chunk_payload.get("chunks", [])
    chunk_texts = [chunk["text"] for chunk in chunks]

    registry_dict = _read_json(registry_path)
    registry = CharacterRegistry(
        narrator_voice=voice_spec_from_dict(registry_dict["narrator_voice"]),
        characters={},
    )

    windows = build_chunk_windows(chunk_texts)

    if director is None:
        from openshelf.config import TTS_ENGINE
        from openshelf.pipeline.engines import create_aligner, create_engine
        from openshelf.pipeline.llm import create_llm

        engine = create_engine(engine_name or TTS_ENGINE)
        aligner = create_aligner(engine, device=device or "cpu")
        director = AudioDirector(engine, create_llm(), aligner, cast_mode=cast_mode)

    registry, directed_chunks = director.direct_chapter(title, windows, registry)
    chapter = build_direction_chapter(
        chunk_payload["number"],
        title,
        chunk_texts,
        directed_chunks,
        fallback_used=director.last_chapter_fallback_used,
        fallback_error=director.last_chapter_error,
    )

    rendition, build_id = _rendition_build_from_build_dir(build_dir)
    resolved_engine = engine_name or getattr(getattr(director, "engine", None), "name", "")
    payload = build_voice_direction_payload(
        rendition, build_id, resolved_engine, cast_mode, [chapter]
    )
    return _write_json_idempotent(direction_path, payload, force=force)


def synth_chapter(
    build_dir: str,
    chapter_number: int,
    engine_name: str | None = None,
    device: str | None = None,
    voice: str | None = None,
    keep_wav: bool = False,
    force: bool = False,
    engine=None,
    aligner=None,
) -> str:
    """Synthesize one chapter's audio from its voice direction and write m4a + sync.

    Reads chapter-NN.voice_direction.json (directed segments) and
    chapter-NN.chunks.json (text + paragraph boundaries), runs TTS + alignment,
    encodes to AAC, and writes chapter-NN.sync.json. Covers both fresh synthesis
    and pause/stitch-policy "restitch" repair (there are no durable WAV/unit
    intermediates to restitch from, so audio is regenerated). Heavy engine/TTS
    deps import lazily.

    Idempotency: if both the m4a and sync artifacts already exist, the stage
    skips unless ``force=True`` (TTS is expensive and not deterministic, so the
    file-exists gate stands in for an input fingerprint).
    """
    from openshelf.pipeline.tts import build_chunk_infos, synthesize_chapter_to_files
    from openshelf.pipeline.voice_director import (
        directed_chunks_from_chapter_direction_artifact,
    )

    chunks_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.chunks.json")
    direction_path = os.path.join(
        build_dir, f"chapter-{chapter_number:02d}.voice_direction.json"
    )
    m4a_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.m4a")
    wav_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.wav")
    sync_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.sync.json")

    for required in (chunks_path, direction_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for synth: {required}")

    if not force and os.path.exists(m4a_path) and os.path.exists(sync_path):
        return sync_path

    chunk_payload = read_chapter_chunks_artifact(chunks_path)
    chunks = chunk_payload.get("chunks", [])
    directed_chunks = directed_chunks_from_chapter_direction_artifact(direction_path)

    chunk_texts = [chunk["text"] for chunk in chunks]
    ends_paragraph = [
        (ci == len(chunks) - 1) or (chunk["para_end"] != chunks[ci + 1]["para_start"])
        for ci, chunk in enumerate(chunks)
    ]
    chunk_infos = build_chunk_infos(chunk_texts, ends_paragraph, directed_chunks)

    if voice is None:
        registry_path = os.path.join(build_dir, "character_registry.json")
        if os.path.exists(registry_path):
            narrator = _read_json(registry_path).get("narrator_voice", {})
            voice = narrator.get("preset_name") or narrator.get("id")

    if engine is None:
        from openshelf.config import TTS_ENGINE
        from openshelf.pipeline.engines import create_aligner, create_engine
        from openshelf.pipeline.tts import load_pipeline

        engine = create_engine(engine_name or TTS_ENGINE)
        device = device or "cpu"
        if engine.name == "kokoro":
            engine.pipeline = load_pipeline(device=device)
        if aligner is None:
            aligner = create_aligner(engine, device=device)

    synthesize_chapter_to_files(
        engine,
        chunk_infos,
        wav_path,
        m4a_path,
        sync_path,
        chapter_number,
        chunk_texts,
        voice=voice,
        aligner=aligner,
        keep_wav=keep_wav,
        force=force,
    )
    return sync_path


def sync_chapter(
    build_dir: str,
    chapter_number: int,
    device: str = "cpu",
    language: str = "en",
    force: bool = False,
) -> str:
    """Re-run WhisperX alignment for one chapter and rewrite chapter-NN.sync.json.

    Reads the finished m4a + chunks.json (for text) and the prior sync.json (for
    the per-chunk ``chunk_audio_starts``, which are produced at synthesis time and
    cannot be recovered from the m4a alone). Avoids LLM and TTS. Heavy aligner
    deps are imported lazily so the offline commands never require them.
    """
    from openshelf.pipeline.tts_engine import WordTimestamp
    from openshelf.pipeline.word_aligner import (
        align_chapter,
        read_chapter_sync_artifact,
        write_chapter_sync_artifact,
    )

    chunks_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.chunks.json")
    sync_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.sync.json")
    m4a_path = os.path.join(build_dir, f"chapter-{chapter_number:02d}.m4a")

    for required in (chunks_path, sync_path, m4a_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required input for sync: {required}")

    chunk_payload = read_chapter_chunks_artifact(chunks_path)
    chunk_texts = [chunk["text"] for chunk in chunk_payload.get("chunks", [])]

    prior_sync = read_chapter_sync_artifact(sync_path)
    chunk_audio_starts = prior_sync["chunk_audio_starts"]

    word_entries = align_chapter(
        m4a_path, chunk_texts, chunk_audio_starts, device=device, language=language
    )

    chunk_words: list[list] = [[] for _ in chunk_texts]
    for entry in word_entries:
        if 0 <= entry.chunk_idx < len(chunk_words):
            chunk_words[entry.chunk_idx].append(
                WordTimestamp(word=entry.word, start=entry.start, end=entry.end)
            )

    return write_chapter_sync_artifact(
        sync_path,
        chapter_number,
        os.path.basename(m4a_path),
        chunk_audio_starts,
        chunk_words,
        chunk_texts=chunk_texts,
        force=force,
    )


def upload_build(
    book_dir: str,
    rendition: str,
    build_id: str,
    bucket: str | None = None,
    source: str | None = None,
    force: bool = False,
    client=None,
) -> list[str]:
    """Publish an existing local build to R2 without rerunning earlier stages.

    Uploads cover/EPUB (when present) and every per-(rendition, build) artifact,
    then updates the mutable book manifest pointer so this build becomes
    ``current_build`` for the rendition (merging with the prior R2 manifest so
    other renditions survive). R2 access is imported lazily so the offline
    commands never require boto3.
    """
    from openshelf.config import R2_BUCKET
    from openshelf.pipeline import r2
    from openshelf.pipeline.manifest import RenditionEntry, merge_book_manifest

    bucket = bucket or R2_BUCKET
    book_dir = os.path.abspath(book_dir)
    title_slug = os.path.basename(book_dir)
    author_slug = os.path.basename(os.path.dirname(book_dir))
    build_dir = os.path.join(book_dir, "audio", rendition, "builds", build_id)

    chapter_data_path = os.path.join(build_dir, "chapter_data.json")
    rendition_manifest_path = os.path.join(build_dir, "rendition-manifest.json")
    character_registry_path = os.path.join(build_dir, "character_registry.json")
    voice_direction_path = os.path.join(build_dir, "voice_direction.json")
    run_context_path = os.path.join(build_dir, "run.json")
    book_manifest_path = os.path.join(book_dir, "manifest.json")

    for required in (chapter_data_path, rendition_manifest_path, book_manifest_path):
        if not os.path.exists(required):
            raise FileNotFoundError(f"missing required artifact for upload: {required}")

    if client is None:
        client = r2.make_client()

    uploaded: list[str] = []

    for ext, content_type in (("jpg", "image/jpeg"), ("png", "image/png")):
        cover_path = os.path.join(book_dir, f"cover.{ext}")
        if os.path.exists(cover_path):
            r2.upload_cover(
                client, bucket, author_slug, title_slug,
                cover_path, content_type, force=force,
            )
            break
    epub_path = os.path.join(book_dir, "book-annotated.epub")
    if os.path.exists(epub_path):
        r2.upload_epub(client, bucket, author_slug, title_slug, epub_path, force=force)

    uploaded += r2.upload_rendition_build(
        client, bucket, author_slug, title_slug, rendition, build_id,
        audio_dir=build_dir,
        chapter_data_path=chapter_data_path,
        rendition_manifest_path=rendition_manifest_path,
        character_registry_path=character_registry_path,
        voice_direction_path=voice_direction_path,
        run_context_path=run_context_path,
        force=force,
    )

    with open(book_manifest_path, "r", encoding="utf-8") as f:
        local_manifest = json.load(f)
    local_entry = local_manifest.get("renditions", {}).get(rendition)
    if local_entry is None:
        raise ValueError(
            f"local book manifest has no rendition '{rendition}': {book_manifest_path}"
        )
    new_entry = RenditionEntry(
        voice=local_entry["voice"],
        engine=local_entry["engine"],
        display=local_entry["display"],
        current_build=build_id,
        available_builds=[build_id],
    )
    prior = r2.fetch_prior_book_manifest(client, bucket, author_slug, title_slug)
    base = prior if prior else local_manifest
    merged = merge_book_manifest(base, rendition, new_entry)
    merged["title"] = local_manifest.get("title", "")
    merged["author"] = local_manifest.get("author", "")
    merged["source"] = source if source is not None else local_manifest.get("source", "")
    with open(book_manifest_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False, sort_keys=True)
    uploaded.append(
        r2.upload_book_manifest(client, bucket, author_slug, title_slug, book_manifest_path)
    )
    return uploaded


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse = subparsers.add_parser("parse")
    parse.add_argument("--epub", required=True)
    parse.add_argument("--out", required=True)
    parse.add_argument("--source", default="")
    parse.add_argument("--force", action="store_true")

    chunk = subparsers.add_parser("chunk")
    chunk.add_argument("--book-parse", required=True)
    chunk.add_argument("--build-dir", required=True)
    chunk.add_argument("--chapters", default=None)
    chunk.add_argument("--force", action="store_true")

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--build-dir", required=True)
    assemble.add_argument("--rendition", required=True)
    assemble.add_argument("--build-id", required=True)
    assemble.add_argument("--output")
    assemble.add_argument("--force", action="store_true")

    coverage = subparsers.add_parser("coverage")
    coverage.add_argument("--build-dir", required=True)
    coverage.add_argument("--json", action="store_true")

    direct = subparsers.add_parser("direct")
    direct.add_argument("--build-dir", required=True)
    direct.add_argument("--chapter", type=int, required=True)
    direct.add_argument("--engine", default=None)
    direct.add_argument("--cast-mode", default="solo", choices=["solo", "multicast"])
    direct.add_argument("--device", default=None)
    direct.add_argument("--force", action="store_true")

    synth = subparsers.add_parser("synth")
    synth.add_argument("--build-dir", required=True)
    synth.add_argument("--chapter", type=int, required=True)
    synth.add_argument("--engine", default=None)
    synth.add_argument("--device", default=None)
    synth.add_argument("--voice", default=None)
    synth.add_argument("--keep-wav", action="store_true")
    synth.add_argument("--force", action="store_true")

    sync = subparsers.add_parser("sync")
    sync.add_argument("--build-dir", required=True)
    sync.add_argument("--chapter", type=int, required=True)
    sync.add_argument("--device", default="cpu")
    sync.add_argument("--language", default="en")
    sync.add_argument("--force", action="store_true")

    upload = subparsers.add_parser("upload")
    upload.add_argument("--book-dir", required=True)
    upload.add_argument("--rendition", required=True)
    upload.add_argument("--build-id", required=True)
    upload.add_argument("--source", default=None)
    upload.add_argument("--force", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "parse":
        path = parse_book(
            epub_path=args.epub,
            output_path=args.out,
            source=args.source,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "chunk":
        try:
            selected = _parse_chapter_filter(args.chapters)
        except ValueError as e:
            parser.error(f"invalid --chapters value: {e}")
        written = chunk_chapters(
            book_parse_path=args.book_parse,
            build_dir=args.build_dir,
            selected_chapters=selected,
            force=args.force,
        )
        for path in written:
            print(path)
        return 0

    if args.command == "assemble":
        path = assemble_chapter_data(
            build_dir=args.build_dir,
            rendition=args.rendition,
            build_id=args.build_id,
            output_path=args.output,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "coverage":
        report = collect_coverage(build_dir=args.build_dir)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(_format_coverage_report(report))
        return 0

    if args.command == "direct":
        path = direct_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            engine_name=args.engine,
            cast_mode=args.cast_mode,
            device=args.device,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "synth":
        path = synth_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            engine_name=args.engine,
            device=args.device,
            voice=args.voice,
            keep_wav=args.keep_wav,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "sync":
        path = sync_chapter(
            build_dir=args.build_dir,
            chapter_number=args.chapter,
            device=args.device,
            language=args.language,
            force=args.force,
        )
        print(path)
        return 0

    if args.command == "upload":
        keys = upload_build(
            book_dir=args.book_dir,
            rendition=args.rendition,
            build_id=args.build_id,
            source=args.source,
            force=args.force,
        )
        for key in keys:
            print(key)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
