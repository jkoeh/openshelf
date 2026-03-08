#!/usr/bin/env python3
"""Convert an EPUB book to MP3 audiobook chapters.

Usage:
    python3 scripts/convert-book.py <epub-path>
    python3 scripts/convert-book.py <epub-path> --output audio/
    python3 scripts/convert-book.py <epub-path> --voice af_heart
    python3 scripts/convert-book.py <epub-path> --dry-run
"""

import argparse
import os
import sys
import time

# Allow running from the scripts/ directory without pip install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.epub_parser import parse_epub
from openshelf.pipeline.text_chunker import chunk_text
from openshelf.pipeline.tts import load_pipeline, synthesize_chapter
from openshelf.pipeline.encoder import encode_to_mp3


def main():
    parser = argparse.ArgumentParser(description="Convert EPUB to MP3 audiobook")
    parser.add_argument("epub", help="Path to EPUB file")
    parser.add_argument("--output", "-o", default="audio", help="Output directory (default: audio/)")
    parser.add_argument("--voice", default=None, help="Kokoro voice ID (default: af_heart)")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk only, no audio")
    parser.add_argument("--keep-wav", action="store_true", help="Keep WAV files after encoding")
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

    for ch in chapters:
        chunks = chunk_text(ch.text)
        print(f"  [{ch.number:>2}/{len(chapters)}] {ch.title} — {ch.word_count:,} words, {len(chunks)} chunks")

    if args.dry_run:
        print("\n[DRY RUN] No audio generated.")
        return

    # Step 2+3+4: Chunk → TTS → MP3
    os.makedirs(args.output, exist_ok=True)

    print(f"\nLoading TTS pipeline ...")
    pipeline = load_pipeline(device=args.device)
    print("Ready.\n")

    total_duration = 0.0
    total_skipped = 0
    start = time.time()

    for ch in chapters:
        mp3_path = os.path.join(args.output, f"chapter-{ch.number:02d}.mp3")

        if os.path.exists(mp3_path):
            print(f"  [{ch.number:>2}/{len(chapters)}] {ch.title} — [SKIP] exists")
            continue

        wav_path = os.path.join(args.output, f"chapter-{ch.number:02d}.wav")
        chunks = chunk_text(ch.text)

        print(f"  [{ch.number:>2}/{len(chapters)}] {ch.title} ({len(chunks)} chunks) ...", end=" ", flush=True)

        synth_kwargs = {}
        if args.voice:
            synth_kwargs["voice"] = args.voice

        result = synthesize_chapter(pipeline, chunks, wav_path, **synth_kwargs)
        duration = encode_to_mp3(wav_path, mp3_path, delete_wav=not args.keep_wav)

        total_duration += duration
        total_skipped += result.skipped_chunks

        mins = duration / 60
        skip_note = f", {result.skipped_chunks} skipped" if result.skipped_chunks else ""
        print(f"{mins:.1f} min{skip_note}")

    elapsed = time.time() - start
    print(f"\nDone. {total_duration / 60:.1f} min of audio in {elapsed:.0f}s.")
    if total_skipped:
        print(f"Warning: {total_skipped} chunks failed TTS and were skipped.")
    print(f"Output: {os.path.abspath(args.output)}/")


if __name__ == "__main__":
    main()
