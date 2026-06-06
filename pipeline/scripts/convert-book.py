#!/usr/bin/env python3
"""Convert an EPUB book to AAC audiobook chapters under a build-versioned layout.

Usage:
    python3 scripts/convert-book.py <epub-path>
    python3 scripts/convert-book.py <epub-path> --output audio/
    python3 scripts/convert-book.py <epub-path> --engine kokoro
    python3 scripts/convert-book.py <epub-path> --voice af_heart
    python3 scripts/convert-book.py <epub-path> --rendition kokoro-af-heart
    python3 scripts/convert-book.py <epub-path> --dry-run
    python3 scripts/convert-book.py <epub-path> --source standard-ebooks --upload

The local output directory mirrors the R2 layout described in
pipeline/docs/step6-r2.md:

    {output}/{author}/{title}/
      book-annotated.epub
      cover.{jpg|png}
      manifest.json                              ← book-level (mutable pointer)
      audio/{rendition}/builds/{build}/
        character_registry.json                  ← voice registry audit/client artifact
        voice_direction.json                     ← speaker + performance audit artifact
        chapter-NN.m4a
        chapter_data.json                        ← inline word timestamps
        rendition-manifest.json                  ← chapter durations + word counts
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
from pathlib import Path

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ebooklib import epub as _epub_lib

from openshelf.config import (
    PIPELINE_VERSION,
    R2_BUCKET,
    REGISTRY_OPENING_CHARS,
    TTS_ENGINE,
    TTS_LANGUAGE,
)
from openshelf.pipeline.build import new_build_id
from openshelf.pipeline.encoder import encode_to_aac
from openshelf.pipeline.engines import create_aligner, create_engine
from openshelf.pipeline.epub_annotator import annotate_epub
from openshelf.pipeline.epub_parser import extract_cover_image, parse_epub
from openshelf.pipeline.llm import create_llm
from openshelf.pipeline.logging_utils import Heartbeat, configure_pipeline_logging
from openshelf.pipeline.manifest import (
    ChapterMeta,
    generate_book_manifest,
    generate_rendition_entry,
    generate_rendition_manifest,
    merge_book_manifest,
)
from openshelf.pipeline.text_chunker import chunk_text
from openshelf.pipeline.tts import ChunkInfo, WordTimestamp, load_pipeline, synthesize_chapter
from openshelf.pipeline.tts_engine import DirectedSegment, VoiceSpec
from openshelf.pipeline.voice_director import AudioDirector, BookContext, ChunkWindow
from openshelf.scrapers.http import sanitize


CHAPTER_DATA_VERSION = 1
logger = logging.getLogger("openshelf.convert_book")


def _log_torch_device_diagnostics(selected_device: str) -> None:
    try:
        import torch
    except ImportError:
        logger.warning("torch is not installed; selected_device=%s", selected_device)
        return

    cuda_available = torch.cuda.is_available()
    device_names = [
        torch.cuda.get_device_name(i)
        for i in range(torch.cuda.device_count())
    ]
    logger.info(
        "Device selected=%s torch=%s cuda_available=%s cuda_version=%s "
        "cuda_device_count=%d cuda_devices=%s",
        selected_device,
        getattr(torch, "__version__", "unknown"),
        cuda_available,
        getattr(torch.version, "cuda", None),
        torch.cuda.device_count(),
        device_names,
    )
    if selected_device == "cpu" and not cuda_available:
        logger.warning(
            "CUDA is not available to torch. If this machine has an NVIDIA GPU, "
            "the project venv likely has a CPU-only PyTorch wheel."
        )


def _display_name_for_voice(voice: str) -> str:
    """Best-effort human label for a voice slug (e.g. af_heart -> Heart)."""
    if "_" in voice:
        return voice.split("_", 1)[1].replace("_", " ").title()
    return voice.title()


def _opening_text(chunked_chapters: list[dict], max_chars: int) -> str:
    parts: list[str] = []
    remaining = max_chars
    for ch_data in chunked_chapters:
        for chunk in ch_data["chunks"]:
            if remaining <= 0:
                return "\n\n".join(parts)
            text = chunk.text[:remaining]
            parts.append(text)
            remaining -= len(text)
    return "\n\n".join(parts)


def _voice_spec_for_override(engine, voice_id: str) -> VoiceSpec:
    for voice in engine.available_voices():
        if voice.id == voice_id or voice.preset_name == voice_id:
            return voice
    if engine.name == "kokoro":
        return VoiceSpec(id=voice_id, preset_name=voice_id)
    return VoiceSpec(id=voice_id, ref_audio_path=voice_id)


def _voice_id_for_manifest(voice: VoiceSpec) -> str:
    return voice.preset_name or voice.id


def _derive_rendition(engine_name: str, voice: VoiceSpec) -> str:
    voice_id = _voice_id_for_manifest(voice).replace("_", "-")
    prefix = f"{engine_name}-"
    if voice_id.startswith(prefix):
        return voice_id
    return f"{engine_name}-{voice_id}"


def _build_chapter_data(
    chunked_chapters: list[dict],
    chapter_chunk_words: dict[int, list[list[WordTimestamp]]],
    rendition: str,
    build_id: str,
) -> dict:
    """Assemble the chapter_data.json payload (chunks + flat words)."""
    payload: dict = {
        "version": CHAPTER_DATA_VERSION,
        "rendition": rendition,
        "build": build_id,
        "chapters": [],
    }
    for ch_data in chunked_chapters:
        ch_num = ch_data["number"]
        chunks = ch_data["chunks"]
        words_per_chunk = chapter_chunk_words.get(ch_num, [[] for _ in chunks])
        entry = {
            "number": ch_num,
            "title": ch_data["title"],
            "word_count": sum(len(c.text.split()) for c in chunks),
            "chunks": [],
        }
        for ci, c in enumerate(chunks):
            words = words_per_chunk[ci] if ci < len(words_per_chunk) else []
            entry["chunks"].append({
                "text": c.text,
                "words": [dataclasses.asdict(w) for w in words],
            })
        payload["chapters"].append(entry)
    return payload


def _directed_segment_to_dict(segment: DirectedSegment) -> dict:
    original_text = segment.original_text if segment.original_text is not None else segment.text
    return {
        "speaker": segment.speaker,
        "voice": dataclasses.asdict(segment.voice),
        "original_text": original_text,
        "synthesis_text": segment.text,
        "emotion": segment.emotion,
        "speed": segment.speed,
        "pause_after_ms": segment.pause_after_ms,
        "delivery_type": segment.delivery_type,
        "voice_policy": segment.voice_policy,
        "join_policy": segment.join_policy,
    }


def _build_direction_chapter(
    ch_data: dict,
    directed_chunks: list[list[DirectedSegment]],
    fallback_used: bool = False,
    fallback_error: str | None = None,
) -> dict:
    payload = {
        "number": ch_data["number"],
        "title": ch_data["title"],
        "fallback_used": fallback_used,
        "fallback_error": fallback_error,
        "chunks": [],
    }
    for ci, c in enumerate(ch_data["chunks"]):
        segments = directed_chunks[ci] if ci < len(directed_chunks) else []
        payload["chunks"].append({
            "index": ci,
            "text": c.text,
            "segments": [_directed_segment_to_dict(segment) for segment in segments],
        })
    return payload


def _build_voice_direction_payload(
    rendition: str,
    build_id: str,
    engine_name: str,
    chapters: list[dict],
) -> dict:
    return {
        "version": 1,
        "rendition": rendition,
        "build": build_id,
        "engine": engine_name,
        "chapters": chapters,
    }


def _chapter_direction_path(build_dir: str, chapter_number: int) -> str:
    return os.path.join(build_dir, f"chapter-{chapter_number:02d}.voice_direction.json")


def _direction_speed_summary(chapter_payload: dict) -> dict:
    speed_counts: dict[str, int] = {}
    total_segments = 0
    above_1x = 0
    narrator_above_1x = 0
    max_speed = 0.0
    for chunk in chapter_payload.get("chunks", []):
        for segment in chunk.get("segments", []):
            total_segments += 1
            speed = float(segment.get("speed", 1.0) or 1.0)
            speed_key = f"{speed:.2f}"
            speed_counts[speed_key] = speed_counts.get(speed_key, 0) + 1
            max_speed = max(max_speed, speed)
            if speed > 1.0:
                above_1x += 1
                if segment.get("speaker") == "narrator":
                    narrator_above_1x += 1
    return {
        "segments": total_segments,
        "speed_counts": speed_counts,
        "above_1x": above_1x,
        "narrator_above_1x": narrator_above_1x,
        "max_speed": max_speed,
    }


def _fetch_prior_book_manifest(client, bucket: str, author_slug: str, title_slug: str) -> dict:
    """Return the prior book manifest on R2 (parsed), or {} if it does not exist."""
    from botocore.exceptions import ClientError

    from openshelf.pipeline import r2_keys

    key = r2_keys.book_manifest_key(author_slug, title_slug)
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {}
        raise
    return json.loads(obj["Body"].read().decode("utf-8"))


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)


def _parse_chapter_filter(value: str | None) -> set[int] | None:
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
                raise argparse.ArgumentTypeError(f"Invalid chapter range: {part}")
            selected.update(range(start, end + 1))
        else:
            number = int(part)
            if number <= 0:
                raise argparse.ArgumentTypeError(f"Invalid chapter number: {part}")
            selected.add(number)
    if not selected:
        raise argparse.ArgumentTypeError("Chapter filter did not contain any chapters")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert EPUB to AAC audiobook")
    parser.add_argument("epub", help="Path to EPUB file")
    parser.add_argument("--output", "-o", default="audio", help="Output directory (default: audio/)")
    parser.add_argument("--source", default="gutenberg", help="Book source (gutenberg, standard-ebooks)")
    parser.add_argument("--engine", default=TTS_ENGINE, help=f"TTS engine (default: {TTS_ENGINE})")
    parser.add_argument("--voice", default=None, help="Narrator voice override; skips LLM narrator selection")
    parser.add_argument("--rendition", default=None, help="Rendition slug (default: derived from engine + narrator)")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument(
        "--chapters",
        default=None,
        help="Local sample filter: chapter number/ranges to generate, e.g. 2 or 2,4-5",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
    parser.add_argument("--upload", action="store_true", help="Upload to Cloudflare R2 after conversion")
    parser.add_argument("--log-dir", default="logs", help="Local log directory (default: logs/)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate audio, chapter_data, and manifests under a fresh build prefix",
    )
    args = parser.parse_args()
    try:
        selected_chapters = _parse_chapter_filter(args.chapters)
    except ValueError as e:
        parser.error(f"Invalid --chapters value: {e}")
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    if not os.path.isfile(args.epub):
        print(f"Error: {args.epub} not found")
        sys.exit(1)

    build_id = new_build_id()
    epub_stem = sanitize(os.path.splitext(os.path.basename(args.epub))[0])
    log_path = configure_pipeline_logging(f"convert-book-{epub_stem}-{build_id}", args.log_dir)
    print(f"Log file: {log_path}")
    logger.info(
        "convert-book started epub=%s source=%s engine=%s rendition=%s upload=%s dry_run=%s",
        args.epub,
        args.source,
        args.engine,
        args.rendition,
        args.upload,
        args.dry_run,
    )

    # Step 1: Parse EPUB
    print(f"Parsing {args.epub} ...")
    with Heartbeat(logger, "Parsing EPUB"):
        chapters = parse_epub(args.epub)
    if not chapters:
        print("No chapters found.")
        logger.error("No chapters found")
        sys.exit(1)

    total_words = sum(ch.word_count for ch in chapters)
    print(f"Found {len(chapters)} chapters ({total_words:,} words)\n")
    logger.info("Parsed %d chapters (%d words)", len(chapters), total_words)

    # Read display metadata from EPUB (title, author for manifest/R2)
    _book_meta = _epub_lib.read_epub(args.epub)
    _title_meta = _book_meta.get_metadata("DC", "title")
    _author_meta = _book_meta.get_metadata("DC", "creator")

    # Step 2: Chunk all chapters
    chunked_chapters = []
    for ch in chapters:
        spoken_ids = [el.id for el in ch.elements if el.spoken]
        chunks = chunk_text(ch.paragraphs, element_ids=spoken_ids)
        chunked_chapters.append({
            "number": ch.number,
            "title": ch.title,
            "chunks": chunks,
        })
        print(f"  [{ch.number:>2}/{len(chapters)}] {ch.title} — {ch.word_count:,} words, {len(chunks)} chunks")

    generation_chapters = chunked_chapters
    if selected_chapters is not None:
        generation_chapters = [
            ch_data for ch_data in chunked_chapters
            if ch_data["number"] in selected_chapters
        ]
        found = {ch_data["number"] for ch_data in generation_chapters}
        missing = sorted(selected_chapters - found)
        if missing:
            parser.error(f"--chapters selected missing chapter(s): {', '.join(str(n) for n in missing)}")
        selected_label = ", ".join(str(ch_data["number"]) for ch_data in generation_chapters)
        print(f"\nChapter filter: generating {selected_label} of {len(chapters)} chapters")
        logger.info(
            "Chapter filter active selected=%s total_chapters=%d",
            sorted(selected_chapters),
            len(chapters),
        )

    # Derive author/title slugs from EPUB path: .../author-slug/title-slug.epub
    epub_parts = os.path.normpath(args.epub).split(os.sep)
    title_slug = sanitize(os.path.splitext(epub_parts[-1])[0])
    author_slug = sanitize(epub_parts[-2]) if len(epub_parts) >= 2 else "unknown"

    book_title = _title_meta[0][0] if _title_meta else title_slug
    book_author = _author_meta[0][0] if _author_meta else author_slug
    book_dir = os.path.join(args.output, author_slug, title_slug)

    print(f"\nBook:        {book_author} — {book_title}")
    print(f"Engine:      {args.engine}")
    print(f"Build:       {build_id}  (pipeline_version={PIPELINE_VERSION})")

    if args.dry_run:
        print("\n[DRY RUN] No audio generated.")
        return

    logger.info("Creating TTS engine and LLM provider")
    engine = create_engine(args.engine)
    selected_device = args.device
    if selected_device is None and (
        engine.name == "kokoro"
        or engine.post_processing_config().needs_forced_alignment
    ):
        from openshelf.pipeline.tts import get_device

        selected_device = get_device()
    selected_device = selected_device or "cpu"
    _log_torch_device_diagnostics(selected_device)

    aligner = create_aligner(engine, device=selected_device)
    llm = create_llm()
    director = AudioDirector(engine, llm, aligner)

    narrator_override = _voice_spec_for_override(engine, args.voice) if args.voice else None
    book_ctx = BookContext(
        title=book_title,
        author=book_author,
        language=TTS_LANGUAGE,
        opening_text=_opening_text(chunked_chapters, REGISTRY_OPENING_CHARS),
    )
    with Heartbeat(logger, "Building character registry with LLM"):
        registry = director.build_registry(book_ctx, narrator_voice_override=narrator_override)
    narrator_voice = registry.narrator_voice
    narrator_voice_id = _voice_id_for_manifest(narrator_voice)
    rendition = args.rendition or _derive_rendition(engine.name, narrator_voice)
    build_dir = os.path.join(book_dir, "audio", rendition, "builds", build_id)
    logger.info(
        "Registry built narrator=%s characters=%d rendition=%s",
        narrator_voice_id,
        len(registry.characters),
        rendition,
    )

    print(f"Rendition:   {rendition}  (narrator={narrator_voice_id})")
    print(f"R2 prefix:   books/{author_slug}/{title_slug}/audio/{rendition}/builds/{build_id}/")
    print(f"Local:       {os.path.abspath(build_dir)}/")
    director.build_dir = Path(build_dir)

    # Annotated EPUB (book-level, immutable, no rendition/build scope)
    os.makedirs(book_dir, exist_ok=True)
    annotated_epub_path = os.path.join(book_dir, "book-annotated.epub")
    if args.force or not os.path.exists(annotated_epub_path):
        annotated_bytes = annotate_epub(args.epub, chapters)
        with open(annotated_epub_path, "wb") as f:
            f.write(annotated_bytes)
        print(f"\nAnnotated EPUB: {annotated_epub_path}")
    else:
        print(f"\nAnnotated EPUB already exists: {annotated_epub_path}")

    # Cover image (book-level)
    cover_path = None
    cover_content_type = "image/jpeg"
    for ext in ("jpg", "png"):
        candidate = os.path.join(book_dir, f"cover.{ext}")
        if os.path.exists(candidate):
            cover_path = candidate
            cover_content_type = "image/png" if ext == "png" else "image/jpeg"
            break
    if not cover_path:
        cover_result = extract_cover_image(args.epub)
        if cover_result:
            img_data, media_type = cover_result
            cover_ext = "png" if "png" in media_type else "jpg"
            cover_path = os.path.join(book_dir, f"cover.{cover_ext}")
            cover_content_type = media_type
            with open(cover_path, "wb") as f:
                f.write(img_data)
            print(f"Cover extracted: {cover_path}")
        else:
            print("No cover image found in EPUB.")

    # Step 3+4: TTS → m4a (per build)
    os.makedirs(build_dir, exist_ok=True)
    character_registry_path = os.path.join(build_dir, "character_registry.json")
    _write_json(character_registry_path, registry.to_dict())
    print(f"Character registry: {character_registry_path}")
    logger.info("Character registry written: %s", character_registry_path)

    voice_direction_path = os.path.join(build_dir, "voice_direction.json")
    voice_direction_payload: dict = _build_voice_direction_payload(
        rendition,
        build_id,
        engine.name,
        [],
    )

    if engine.name == "kokoro":
        print(f"\nLoading TTS pipeline on {selected_device} ...")
        with Heartbeat(logger, f"Loading Kokoro pipeline on {selected_device}"):
            engine.pipeline = load_pipeline(device=selected_device)
        print("Ready.\n")
        logger.info("Kokoro pipeline ready on %s", selected_device)
    else:
        print(f"\nUsing TTS engine: {engine.name}\n")
        logger.info("Using TTS engine: %s", engine.name)

    total_duration = 0.0
    total_skipped = 0
    chapter_durations: dict[int, float] = {}
    chapter_chunk_starts: dict[int, list[float]] = {}
    chapter_chunk_words: dict[int, list[list[WordTimestamp]]] = {}
    start = time.time()

    for ch_data in generation_chapters:
        ch_num = ch_data["number"]
        ch_title = ch_data["title"]
        chunks = ch_data["chunks"]

        print(f"  [{ch_num:>2}/{len(chapters)}] {ch_title} ({len(chunks)} chunks)")
        logger.info("Chapter %d/%d direction started: %s (%d chunks)", ch_num, len(chapters), ch_title, len(chunks))

        chunk_infos = []
        with Heartbeat(logger, f"Directing chapter {ch_num}") as heartbeat:
            heartbeat.update(f"Attributing speakers for chapter {ch_num} ({len(chunks)} chunks)")
            windows = [
                ChunkWindow(
                    prev=chunks[ci - 1].text if ci > 0 else "",
                    text=c.text,
                    next=chunks[ci + 1].text if ci < len(chunks) - 1 else "",
                )
                for ci, c in enumerate(chunks)
            ]
            registry, directed_chunks = director.direct_chapter(ch_title, windows, registry)
            heartbeat.update(f"Preparing directed chunk metadata for chapter {ch_num}")
            for ci, c in enumerate(chunks):
                ends_para = (ci == len(chunks) - 1) or (c.para_end != chunks[ci + 1].para_start)
                chunk_infos.append(ChunkInfo(
                    text=c.text,
                    ends_paragraph=ends_para,
                    directed_segments=directed_chunks[ci],
                ))
            direction_chapter = _build_direction_chapter(
                ch_data,
                directed_chunks,
                fallback_used=director.last_chapter_fallback_used,
                fallback_error=director.last_chapter_error,
            )
            voice_direction_payload["chapters"].append(direction_chapter)
            chapter_direction_path = _chapter_direction_path(build_dir, ch_num)
            _write_json(
                chapter_direction_path,
                _build_voice_direction_payload(
                    rendition,
                    build_id,
                    engine.name,
                    [direction_chapter],
                ),
            )
            speed_summary = _direction_speed_summary(direction_chapter)
            logger.info(
                "Chapter %d direction snapshot written: %s",
                ch_num,
                chapter_direction_path,
            )
            logger.info(
                "Chapter %d direction speed summary segments=%d speeds=%s "
                "above_1x=%d narrator_above_1x=%d max_speed=%.2f",
                ch_num,
                speed_summary["segments"],
                speed_summary["speed_counts"],
                speed_summary["above_1x"],
                speed_summary["narrator_above_1x"],
                speed_summary["max_speed"],
            )
        logger.info("Chapter %d direction complete", ch_num)

        m4a_path = os.path.join(build_dir, f"chapter-{ch_num:02d}.m4a")
        wav_path = os.path.join(build_dir, f"chapter-{ch_num:02d}.wav")

        # Each pipeline run gets a fresh random build_dir, so any existing
        # m4a here is leftover from an aborted prior attempt against this same
        # ID — regenerate unconditionally rather than risk pairing stale audio
        # with freshly-generated chapter_data word timestamps.
        if os.path.exists(m4a_path):
            os.remove(m4a_path)

        print("    synthesizing + aligning ...", end=" ", flush=True)

        with Heartbeat(logger, f"Synthesizing and aligning chapter {ch_num}"):
            result = synthesize_chapter(
                engine,
                chunk_infos,
                wav_path,
                voice=narrator_voice_id,
                aligner=aligner,
            )
        with Heartbeat(logger, f"Encoding chapter {ch_num} to AAC"):
            duration = encode_to_aac(wav_path, m4a_path, delete_wav=not args.keep_wav)

        chapter_durations[ch_num] = duration
        chapter_chunk_starts[ch_num] = result.chunk_audio_starts
        chapter_chunk_words[ch_num] = result.chunk_words
        total_duration += duration
        total_skipped += result.skipped_chunks

        mins = duration / 60
        skip_note = f", {result.skipped_chunks} skipped" if result.skipped_chunks else ""
        print(f"{mins:.1f} min{skip_note}")
        logger.info(
            "Chapter %d complete duration=%.2fs skipped_chunks=%d m4a=%s",
            ch_num,
            duration,
            result.skipped_chunks,
            m4a_path,
        )

    elapsed = time.time() - start
    print(f"\nDone. {total_duration / 60:.1f} min of audio in {elapsed:.0f}s.")
    if total_skipped:
        print(f"Warning: {total_skipped} chunks failed TTS and were skipped.")
        logger.warning("%d chunks failed TTS and were skipped", total_skipped)
    print(f"Output: {os.path.abspath(build_dir)}/")
    logger.info("Audio generation complete duration=%.2fs elapsed=%.2fs output=%s", total_duration, elapsed, build_dir)

    _write_json(character_registry_path, registry.to_dict())
    _write_json(voice_direction_path, voice_direction_payload)
    print(f"Voice direction: {voice_direction_path}")
    logger.info("Character registry finalized: %s", character_registry_path)
    logger.info("Voice direction written: %s", voice_direction_path)

    # Step 5a (book) / 5c (rendition): build manifests
    word_count_map = {ch.number: ch.word_count for ch in chapters}
    chapter_metas = [
        ChapterMeta(
            number=ch_data["number"],
            title=ch_data["title"],
            filename=f"chapter-{ch_data['number']:02d}.m4a",
            duration_seconds=chapter_durations.get(ch_data["number"], 0.0),
            word_count=word_count_map.get(ch_data["number"], 0),
        )
        for ch_data in generation_chapters
        if ch_data["number"] in chapter_durations
    ]

    rendition_manifest_path = os.path.join(build_dir, "rendition-manifest.json")
    generate_rendition_manifest(
        rendition=rendition,
        build_id=build_id,
        voice=narrator_voice_id,
        engine=engine.name,
        pipeline_version=PIPELINE_VERSION,
        chapters=chapter_metas,
        output_dir=build_dir,
    )
    print(f"Rendition manifest: {rendition_manifest_path}")
    logger.info("Rendition manifest written: %s", rendition_manifest_path)

    chapter_data_path = os.path.join(build_dir, "chapter_data.json")
    payload = _build_chapter_data(
        generation_chapters, chapter_chunk_words, rendition, build_id,
    )
    _write_json(chapter_data_path, payload)
    print(f"Chapter data: {chapter_data_path}")
    logger.info("Chapter data written: %s", chapter_data_path)

    # Book-level manifest (mutable pointer). Written from scratch here with only
    # this rendition; merged with the prior R2 manifest at upload time so other
    # renditions are preserved.
    new_entry = generate_rendition_entry(
        voice=narrator_voice_id,
        engine=engine.name,
        display=_display_name_for_voice(narrator_voice_id),
        build_id=build_id,
    )
    book_manifest_path = generate_book_manifest(
        author=book_author,
        title=book_title,
        source=args.source,
        renditions={rendition: new_entry},
        output_dir=book_dir,
    )
    print(f"Book manifest (local): {book_manifest_path}")
    logger.info("Book manifest written: %s", book_manifest_path)

    # Step 6: Upload to R2
    if args.upload:
        from openshelf.pipeline.r2 import (
            make_client,
            upload_book_manifest,
            upload_cover,
            upload_epub,
            upload_rendition_build,
        )

        print("\nUploading to R2 ...")
        logger.info("Uploading to R2 bucket=%s prefix=books/%s/%s/", R2_BUCKET, author_slug, title_slug)
        with Heartbeat(logger, "Creating R2 client"):
            client = make_client()

        if cover_path:
            with Heartbeat(logger, "Uploading cover to R2"):
                upload_cover(client, R2_BUCKET, author_slug, title_slug,
                             cover_path, cover_content_type, force=args.force)
        with Heartbeat(logger, "Uploading annotated EPUB to R2"):
            upload_epub(client, R2_BUCKET, author_slug, title_slug,
                        annotated_epub_path, force=args.force)
        with Heartbeat(logger, "Uploading rendition build to R2"):
            upload_rendition_build(
                client, R2_BUCKET, author_slug, title_slug,
                rendition, build_id,
                audio_dir=build_dir,
                chapter_data_path=chapter_data_path,
                rendition_manifest_path=rendition_manifest_path,
                character_registry_path=character_registry_path,
                voice_direction_path=voice_direction_path,
                force=args.force,
            )

        # Merge with prior R2 manifest so other renditions survive.
        with Heartbeat(logger, "Fetching prior book manifest from R2"):
            prior = _fetch_prior_book_manifest(client, R2_BUCKET, author_slug, title_slug)
        if prior:
            merged = merge_book_manifest(prior, rendition, new_entry)
            merged["title"] = book_title
            merged["author"] = book_author
            merged["source"] = args.source
            _write_json(book_manifest_path, merged)
            logger.info("Merged prior R2 manifest with new rendition entry")

        with Heartbeat(logger, "Uploading book manifest to R2"):
            upload_book_manifest(client, R2_BUCKET, author_slug, title_slug, book_manifest_path)

        print("Upload complete.")
        print(f"R2 prefix: books/{author_slug}/{title_slug}/")
        logger.info("Upload complete: books/%s/%s/", author_slug, title_slug)


if __name__ == "__main__":
    main()
