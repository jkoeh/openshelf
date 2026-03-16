#!/usr/bin/env python3
"""Convert an EPUB book to MP3 audiobook chapters.

Usage:
    python3 scripts/convert-book.py <epub-path>
    python3 scripts/convert-book.py <epub-path> --output audio/
    python3 scripts/convert-book.py <epub-path> --voice af_heart
    python3 scripts/convert-book.py <epub-path> --rendition kokoro-af-heart
    python3 scripts/convert-book.py <epub-path> --dry-run
    python3 scripts/convert-book.py <epub-path> --source standard-ebooks --upload
"""

import argparse
import json
import os
import sys
import time

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ebooklib import epub as _epub_lib

from openshelf.config import R2_BUCKET, R2_DEFAULT_RENDITION
from openshelf.pipeline.alignment import build_alignment, write_alignment
from openshelf.pipeline.epub_annotator import annotate_epub
from openshelf.pipeline.epub_parser import parse_epub
from openshelf.pipeline.manifest import ChapterMeta, generate_manifest
from openshelf.pipeline.text_chunker import chunk_text, serialize_chunks, sha256_file
from openshelf.pipeline.tts import load_pipeline, synthesize_chapter
from openshelf.pipeline.encoder import audio_duration, encode_to_mp3
from openshelf.scrapers.http import sanitize


def main():
    parser = argparse.ArgumentParser(description="Convert EPUB to MP3 audiobook")
    parser.add_argument("epub", help="Path to EPUB file")
    parser.add_argument("--output", "-o", default="audio", help="Output directory (default: audio/)")
    parser.add_argument("--source", default="gutenberg", help="Book source (gutenberg, standard-ebooks)")
    parser.add_argument("--voice", default=None, help="Kokoro voice ID (default: af_heart)")
    parser.add_argument("--rendition", default=R2_DEFAULT_RENDITION, help=f"Rendition name (default: {R2_DEFAULT_RENDITION})")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
    parser.add_argument("--upload", action="store_true", help="Upload to Cloudflare R2 after conversion")
    args = parser.parse_args()

    if not os.path.isfile(args.epub):
        print(f"Error: {args.epub} not found")
        sys.exit(1)

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
    output_dir = os.path.join(book_dir, args.rendition)

    book_title = _title_meta[0][0] if _title_meta else title_slug
    book_author = _author_meta[0][0] if _author_meta else author_slug

    print(f"\nBook:     {book_author} — {book_title}")
    print(f"R2 keys:  books/{author_slug}/{title_slug}/audio/{args.rendition}/chapter-*.mp3")
    print(f"Local:    {os.path.abspath(output_dir)}/")

    if args.dry_run:
        print("\n[DRY RUN] No audio generated.")
        return

    # Write annotated EPUB (with stable element IDs injected for client sync)
    os.makedirs(book_dir, exist_ok=True)
    annotated_epub_path = os.path.join(book_dir, "book-annotated.epub")
    if not os.path.exists(annotated_epub_path):
        annotated_bytes = annotate_epub(args.epub, chapters)
        with open(annotated_epub_path, "wb") as f:
            f.write(annotated_bytes)
        print(f"\nAnnotated EPUB: {annotated_epub_path}")
    else:
        print(f"\nAnnotated EPUB already exists: {annotated_epub_path}")

    # Write chunks.json at the book level (shared across renditions)
    chunks_path = os.path.join(book_dir, "chunks.json")
    if not os.path.exists(chunks_path):
        epub_sha = sha256_file(args.epub)
        chunks_json = serialize_chunks(chunked_chapters, epub_sha)
        with open(chunks_path, "w", encoding="utf-8") as f:
            f.write(chunks_json)
        print(f"\nChunks written: {chunks_path}")
    else:
        print(f"\nChunks already exist: {chunks_path}")

    # Step 3+4: TTS → MP3
    os.makedirs(output_dir, exist_ok=True)

    from openshelf.pipeline.tts import get_device
    device = args.device or get_device()
    print(f"\nLoading TTS pipeline on {device} ...")
    pipeline = load_pipeline(device=device)
    print("Ready.\n")

    total_duration = 0.0
    total_skipped = 0
    chapter_durations: dict[int, float] = {}
    alignment_chapters: list[dict] = []
    start = time.time()

    for ch_data in chunked_chapters:
        ch_num = ch_data["number"]
        ch_title = ch_data["title"]
        chunks = ch_data["chunks"]
        chunk_texts = [c.text for c in chunks]

        mp3_path = os.path.join(output_dir, f"chapter-{ch_num:02d}.mp3")

        if os.path.exists(mp3_path):
            duration = audio_duration(mp3_path)
            chapter_durations[ch_num] = duration
            # Timing unknown for pre-existing MP3s — all starts marked as -1.0
            alignment_chapters.append({
                "number": ch_num,
                "total_duration_s": duration,
                "chunk_audio_starts": [-1.0] * len(chunks),
            })
            print(f"  [{ch_num:>2}/{len(chapters)}] {ch_title} — [SKIP] exists")
            continue

        wav_path = os.path.join(output_dir, f"chapter-{ch_num:02d}.wav")

        print(f"  [{ch_num:>2}/{len(chapters)}] {ch_title} ({len(chunks)} chunks) ...", end=" ", flush=True)

        synth_kwargs = {}
        if args.voice:
            synth_kwargs["voice"] = args.voice

        result = synthesize_chapter(pipeline, chunk_texts, wav_path, **synth_kwargs)
        duration = encode_to_mp3(wav_path, mp3_path, delete_wav=not args.keep_wav)

        chapter_durations[ch_num] = duration
        alignment_chapters.append({
            "number": ch_num,
            "total_duration_s": duration,
            "chunk_audio_starts": result.chunk_audio_starts,
        })
        total_duration += duration
        total_skipped += result.skipped_chunks

        mins = duration / 60
        skip_note = f", {result.skipped_chunks} skipped" if result.skipped_chunks else ""
        print(f"{mins:.1f} min{skip_note}")

    elapsed = time.time() - start
    print(f"\nDone. {total_duration / 60:.1f} min of audio in {elapsed:.0f}s.")
    if total_skipped:
        print(f"Warning: {total_skipped} chunks failed TTS and were skipped.")
    print(f"Output: {os.path.abspath(output_dir)}/")

    # Step 5: Generate manifest
    with open(chunks_path, encoding="utf-8") as f:
        chunks_data = json.load(f)
    word_count_map = {ch.number: ch.word_count for ch in chapters}
    chapter_metas = [
        ChapterMeta(
            number=ch_data["number"],
            title=ch_data["title"],
            filename=f"chapter-{ch_data['number']:02d}.mp3",
            duration_seconds=chapter_durations.get(ch_data["number"], 0.0),
            word_count=word_count_map.get(ch_data["number"], 0),
        )
        for ch_data in chunked_chapters
        if ch_data["number"] in chapter_durations
    ]
    manifest_path = generate_manifest(
        author=book_author,
        title=book_title,
        source=args.source,
        chapters=chapter_metas,
        output_dir=output_dir,
        rendition=args.rendition,
        chunks_version=chunks_data.get("version", 1),
    )
    print(f"Manifest: {manifest_path}")

    # Step 5b: Generate alignment.json
    alignment_path = os.path.join(output_dir, "alignment.json")
    alignment = build_alignment(rendition=args.rendition, chapters=alignment_chapters)
    write_alignment(alignment, alignment_path)
    print(f"Alignment: {alignment_path}")

    # Step 6: Upload to R2
    if args.upload:
        from openshelf.pipeline.r2 import make_client, upload_epub, upload_chunks, upload_rendition, upload_alignment
        print("\nUploading to R2 ...")
        client = make_client()
        upload_epub(client, R2_BUCKET, author_slug, title_slug, annotated_epub_path)
        upload_chunks(client, R2_BUCKET, author_slug, title_slug, chunks_path)
        upload_rendition(client, R2_BUCKET, author_slug, title_slug, args.rendition, output_dir, manifest_path)
        upload_alignment(client, R2_BUCKET, author_slug, title_slug, args.rendition, alignment_path)
        print("Upload complete.")
        print(f"R2 prefix: books/{author_slug}/{title_slug}/")


if __name__ == "__main__":
    main()
