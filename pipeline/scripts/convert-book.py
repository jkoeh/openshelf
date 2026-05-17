#!/usr/bin/env python3
"""Convert an EPUB book to AAC audiobook chapters under a build-versioned layout.

Usage:
    python3 scripts/convert-book.py <epub-path>
    python3 scripts/convert-book.py <epub-path> --output audio/
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
        chapter-NN.m4a
        chapter_data.json                        ← inline word timestamps
        rendition-manifest.json                  ← chapter durations + word counts
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ebooklib import epub as _epub_lib

from openshelf.config import (
    PIPELINE_VERSION,
    R2_BUCKET,
    R2_DEFAULT_RENDITION,
    TTS_VOICE,
)
from openshelf.pipeline.build import new_build_id
from openshelf.pipeline.encoder import encode_to_aac
from openshelf.pipeline.epub_annotator import annotate_epub
from openshelf.pipeline.epub_parser import extract_cover_image, parse_epub
from openshelf.pipeline.manifest import (
    ChapterMeta,
    generate_book_manifest,
    generate_rendition_entry,
    generate_rendition_manifest,
    merge_book_manifest,
)
from openshelf.pipeline.text_chunker import chunk_text
from openshelf.pipeline.tts import ChunkInfo, WordTimestamp, load_pipeline, synthesize_chapter
from openshelf.scrapers.http import sanitize


CHAPTER_DATA_VERSION = 1
ENGINE = "kokoro"


def _display_name_for_voice(voice: str) -> str:
    """Best-effort human label for a voice slug (e.g. af_heart -> Heart)."""
    if "_" in voice:
        return voice.split("_", 1)[1].replace("_", " ").title()
    return voice.title()


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert EPUB to AAC audiobook")
    parser.add_argument("epub", help="Path to EPUB file")
    parser.add_argument("--output", "-o", default="audio", help="Output directory (default: audio/)")
    parser.add_argument("--source", default="gutenberg", help="Book source (gutenberg, standard-ebooks)")
    parser.add_argument("--voice", default=None, help="Kokoro voice ID (default: af_heart)")
    parser.add_argument("--rendition", default=R2_DEFAULT_RENDITION, help=f"Rendition slug (default: {R2_DEFAULT_RENDITION})")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
    parser.add_argument("--upload", action="store_true", help="Upload to Cloudflare R2 after conversion")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite local artifacts and R2 keys (regenerates audio, chapter_data, manifest)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.epub):
        print(f"Error: {args.epub} not found")
        sys.exit(1)

    voice = args.voice or TTS_VOICE
    build_id = new_build_id()

    # Step 1: Parse EPUB
    print(f"Parsing {args.epub} ...")
    chapters = parse_epub(args.epub)
    if not chapters:
        print("No chapters found.")
        sys.exit(1)

    total_words = sum(ch.word_count for ch in chapters)
    print(f"Found {len(chapters)} chapters ({total_words:,} words)\n")

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

    # Derive author/title slugs from EPUB path: .../author-slug/title-slug.epub
    epub_parts = os.path.normpath(args.epub).split(os.sep)
    title_slug = sanitize(os.path.splitext(epub_parts[-1])[0])
    author_slug = sanitize(epub_parts[-2]) if len(epub_parts) >= 2 else "unknown"

    book_dir = os.path.join(args.output, author_slug, title_slug)
    build_dir = os.path.join(book_dir, "audio", args.rendition, "builds", build_id)

    book_title = _title_meta[0][0] if _title_meta else title_slug
    book_author = _author_meta[0][0] if _author_meta else author_slug

    print(f"\nBook:        {book_author} — {book_title}")
    print(f"Rendition:   {args.rendition}  (voice={voice})")
    print(f"Build:       {build_id}  (pipeline_version={PIPELINE_VERSION})")
    print(f"R2 prefix:   books/{author_slug}/{title_slug}/audio/{args.rendition}/builds/{build_id}/")
    print(f"Local:       {os.path.abspath(build_dir)}/")

    if args.dry_run:
        print("\n[DRY RUN] No audio generated.")
        return

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

    from openshelf.pipeline.tts import get_device
    device = args.device or get_device()
    print(f"\nLoading TTS pipeline on {device} ...")
    pipeline = load_pipeline(device=device)
    print("Ready.\n")

    total_duration = 0.0
    total_skipped = 0
    chapter_durations: dict[int, float] = {}
    chapter_chunk_starts: dict[int, list[float]] = {}
    chapter_chunk_words: dict[int, list[list[WordTimestamp]]] = {}
    start = time.time()

    for ch_data in chunked_chapters:
        ch_num = ch_data["number"]
        ch_title = ch_data["title"]
        chunks = ch_data["chunks"]

        chunk_infos = []
        for ci, c in enumerate(chunks):
            ends_para = (ci == len(chunks) - 1) or (c.para_end != chunks[ci + 1].para_start)
            chunk_infos.append(ChunkInfo(text=c.text, ends_paragraph=ends_para))

        m4a_path = os.path.join(build_dir, f"chapter-{ch_num:02d}.m4a")
        wav_path = os.path.join(build_dir, f"chapter-{ch_num:02d}.wav")

        # Each pipeline run gets a fresh random build_dir, so any existing
        # m4a here is leftover from an aborted prior attempt against this same
        # ID — regenerate unconditionally rather than risk pairing stale audio
        # with freshly-generated chapter_data word timestamps.
        if os.path.exists(m4a_path):
            os.remove(m4a_path)

        print(f"  [{ch_num:>2}/{len(chapters)}] {ch_title} ({len(chunks)} chunks) ...", end=" ", flush=True)

        synth_kwargs = {}
        if args.voice:
            synth_kwargs["voice"] = args.voice

        result = synthesize_chapter(pipeline, chunk_infos, wav_path, **synth_kwargs)
        duration = encode_to_aac(wav_path, m4a_path, delete_wav=not args.keep_wav)

        chapter_durations[ch_num] = duration
        chapter_chunk_starts[ch_num] = result.chunk_audio_starts
        chapter_chunk_words[ch_num] = result.chunk_words
        total_duration += duration
        total_skipped += result.skipped_chunks

        mins = duration / 60
        skip_note = f", {result.skipped_chunks} skipped" if result.skipped_chunks else ""
        print(f"{mins:.1f} min{skip_note}")

    elapsed = time.time() - start
    print(f"\nDone. {total_duration / 60:.1f} min of audio in {elapsed:.0f}s.")
    if total_skipped:
        print(f"Warning: {total_skipped} chunks failed TTS and were skipped.")
    print(f"Output: {os.path.abspath(build_dir)}/")

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
        for ch_data in chunked_chapters
        if ch_data["number"] in chapter_durations
    ]

    rendition_manifest_path = os.path.join(build_dir, "rendition-manifest.json")
    generate_rendition_manifest(
        rendition=args.rendition,
        build_id=build_id,
        voice=voice,
        engine=ENGINE,
        pipeline_version=PIPELINE_VERSION,
        chapters=chapter_metas,
        output_dir=build_dir,
    )
    print(f"Rendition manifest: {rendition_manifest_path}")

    chapter_data_path = os.path.join(build_dir, "chapter_data.json")
    payload = _build_chapter_data(
        chunked_chapters, chapter_chunk_words, args.rendition, build_id,
    )
    _write_json(chapter_data_path, payload)
    print(f"Chapter data: {chapter_data_path}")

    # Book-level manifest (mutable pointer). Written from scratch here with only
    # this rendition; merged with the prior R2 manifest at upload time so other
    # renditions are preserved.
    new_entry = generate_rendition_entry(
        voice=voice,
        engine=ENGINE,
        display=_display_name_for_voice(voice),
        build_id=build_id,
    )
    book_manifest_path = generate_book_manifest(
        author=book_author,
        title=book_title,
        source=args.source,
        renditions={args.rendition: new_entry},
        output_dir=book_dir,
    )
    print(f"Book manifest (local): {book_manifest_path}")

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
        client = make_client()

        if cover_path:
            upload_cover(client, R2_BUCKET, author_slug, title_slug,
                         cover_path, cover_content_type, force=args.force)
        upload_epub(client, R2_BUCKET, author_slug, title_slug,
                    annotated_epub_path, force=args.force)
        upload_rendition_build(
            client, R2_BUCKET, author_slug, title_slug,
            args.rendition, build_id,
            audio_dir=build_dir,
            chapter_data_path=chapter_data_path,
            rendition_manifest_path=rendition_manifest_path,
            force=args.force,
        )

        # Merge with prior R2 manifest so other renditions survive.
        prior = _fetch_prior_book_manifest(client, R2_BUCKET, author_slug, title_slug)
        if prior:
            merged = merge_book_manifest(prior, args.rendition, new_entry)
            merged["title"] = book_title
            merged["author"] = book_author
            merged["source"] = args.source
            _write_json(book_manifest_path, merged)

        upload_book_manifest(client, R2_BUCKET, author_slug, title_slug, book_manifest_path)

        print("Upload complete.")
        print(f"R2 prefix: books/{author_slug}/{title_slug}/")


if __name__ == "__main__":
    main()
