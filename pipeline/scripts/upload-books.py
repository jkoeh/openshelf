#!/usr/bin/env python3
"""Upload already-generated audiobook files to Cloudflare R2.

Generates manifest.json from existing Opus files (via ffprobe) if not already present,
then uploads epub, chapter_data.json, and rendition (m4a files + manifest) to R2.
Chapter titles and word counts are sourced from chapter_data.json.

Usage:
    python3 scripts/upload-books.py <epub-path>
    python3 scripts/upload-books.py <epub-path> --source gutenberg
    python3 scripts/upload-books.py <epub-path> --rendition kokoro-af-heart
"""

import argparse
import json
import os
import sys

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ebooklib import epub as _epub_lib

from openshelf.config import R2_BUCKET, R2_DEFAULT_RENDITION
from openshelf.pipeline.encoder import audio_duration
from openshelf.pipeline.manifest import ChapterMeta, generate_manifest
from openshelf.pipeline.r2 import make_client, upload_epub, upload_rendition, upload_chapter_data, upload_word_alignment
from openshelf.scrapers.http import sanitize



def main():
    parser = argparse.ArgumentParser(description="Upload generated audiobook to Cloudflare R2")
    parser.add_argument("epub", help="Path to source EPUB file")
    parser.add_argument("--output", "-o", default="audio", help="Audio output directory (default: audio/)")
    parser.add_argument("--source", default="gutenberg", help="Book source (gutenberg, standard-ebooks)")
    parser.add_argument("--rendition", default=R2_DEFAULT_RENDITION, help=f"Rendition name (default: {R2_DEFAULT_RENDITION})")
    args = parser.parse_args()

    if not os.path.isfile(args.epub):
        print(f"Error: EPUB not found: {args.epub}")
        sys.exit(1)

    # Derive slugs the same way convert-book.py does
    epub_parts = os.path.normpath(args.epub).split(os.sep)
    title_slug = sanitize(os.path.splitext(epub_parts[-1])[0])
    author_slug = sanitize(epub_parts[-2]) if len(epub_parts) >= 2 else "unknown"
    book_dir = os.path.join(args.output, author_slug, title_slug)
    rendition_dir = os.path.join(book_dir, args.rendition)
    chapter_data_path = os.path.join(rendition_dir, "chapter_data.json")

    if not os.path.isdir(rendition_dir):
        print(f"Error: rendition directory not found: {rendition_dir}")
        sys.exit(1)

    if not os.path.isfile(chapter_data_path):
        print(f"Error: chapter_data.json not found: {chapter_data_path}")
        print("Run convert-book.py first to generate it.")
        sys.exit(1)

    # Read display metadata from EPUB
    _book_meta = _epub_lib.read_epub(args.epub)
    _title_meta = _book_meta.get_metadata("DC", "title")
    _author_meta = _book_meta.get_metadata("DC", "creator")
    book_title = _title_meta[0][0] if _title_meta else title_slug
    book_author = _author_meta[0][0] if _author_meta else author_slug

    print(f"Book:     {book_author} — {book_title}")
    print(f"Rendition: {rendition_dir}")

    # Load chapter_data.json for chapter titles and word counts
    with open(chapter_data_path, encoding="utf-8") as f:
        chapter_data = json.load(f)
    title_map = {ch["number"]: ch["title"] for ch in chapter_data.get("chapters", [])}
    word_count_map = {
        ch["number"]: ch.get("word_count", 0) for ch in chapter_data.get("chapters", [])
    }

    # Find all m4a files and get durations via ffprobe
    audio_files = sorted(f for f in os.listdir(rendition_dir) if f.endswith(".m4a"))
    if not audio_files:
        print(f"Error: no M4A files found in {rendition_dir}")
        sys.exit(1)

    print(f"\nReading durations for {len(audio_files)} chapters ...")
    chapter_metas: list[ChapterMeta] = []
    for filename in audio_files:
        try:
            ch_num = int(filename.replace("chapter-", "").replace(".m4a", ""))
        except ValueError:
            continue
        audio_path = os.path.join(rendition_dir, filename)
        duration = audio_duration(audio_path)
        chapter_metas.append(ChapterMeta(
            number=ch_num,
            title=title_map.get(ch_num, f"Chapter {ch_num}"),
            filename=filename,
            duration_seconds=duration,
            word_count=word_count_map.get(ch_num, 0),
        ))
        print(f"  {filename}: {duration / 60:.1f} min")

    total_hours = sum(cm.duration_seconds for cm in chapter_metas) / 3600
    print(f"\nTotal: {total_hours:.1f} hours across {len(chapter_metas)} chapters")

    # Step 5: Generate manifest (idempotent — skips if already exists)
    manifest_path = generate_manifest(
        author=book_author,
        title=book_title,
        source=args.source,
        chapters=chapter_metas,
        output_dir=rendition_dir,
        rendition=args.rendition,
    )
    print(f"Manifest: {manifest_path}")

    # Step 6: Upload to R2
    print("\nUploading to R2 ...")
    client = make_client()
    upload_epub(client, R2_BUCKET, author_slug, title_slug, args.epub)
    upload_rendition(client, R2_BUCKET, author_slug, title_slug, args.rendition, rendition_dir, manifest_path)
    upload_chapter_data(client, R2_BUCKET, author_slug, title_slug, args.rendition, chapter_data_path)

    alignment_path = os.path.join(rendition_dir, "word_alignment.json")
    if os.path.isfile(alignment_path):
        upload_word_alignment(client, R2_BUCKET, author_slug, title_slug, args.rendition, alignment_path)
        print("Word alignment uploaded.")
    else:
        print("Note: word_alignment.json not present (only generated with convert-book.py --whisperx).")

    print("Upload complete.")
    print(f"R2 prefix: books/{author_slug}/{title_slug}/")


if __name__ == "__main__":
    main()
