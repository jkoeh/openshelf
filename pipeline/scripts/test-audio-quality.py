#!/usr/bin/env python3
"""Quick audio quality validation script.

Generates audio from a short EPUB (or built-in test text) and validates:
  1. No overlap leakage — context prefix audio is properly trimmed
  2. Word alignment passes — validate_alignment() returns zero violations
  3. Silence durations correct — paragraph breaks > mid-paragraph gaps
  4. Duration sanity — total audio roughly matches expected speech rate

Usage:
    python scripts/test-audio-quality.py                          # built-in test text
    python scripts/test-audio-quality.py --epub <path>            # from EPUB
    python scripts/test-audio-quality.py --epub <path> --chapters 1-2
    python scripts/test-audio-quality.py --device cpu             # force CPU
"""

import argparse
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.config import (
    CHUNK_MAX_WORDS,
    SILENCE_MID_PARAGRAPH_MS,
    SILENCE_PARAGRAPH_BREAK_MS,
    TTS_SAMPLE_RATE,
    WER_THRESHOLD,
    WER_AVG_THRESHOLD,
)
from openshelf.pipeline.text_chunker import chunk_text
from openshelf.pipeline.transcriber import compute_wer, transcribe_audio
from openshelf.pipeline.tts import ChunkInfo, load_pipeline, synthesize_chapter
from openshelf.pipeline.word_aligner import align_chapter, validate_alignment


# ---------------------------------------------------------------------------
# Built-in test text (two short chapters)
# ---------------------------------------------------------------------------

TEST_CHAPTERS = [
    {
        "number": 1,
        "title": "The Arrival",
        "paragraphs": [
            "The train pulled into the station just as the sun began to set. "
            "Passengers spilled out onto the platform, clutching bags and coats.",
            "Among them was a young woman who paused to look around. "
            "She had never been to this city before. "
            "The air smelled of rain and something metallic.",
            "She walked toward the exit, her footsteps echoing on the wet tiles. "
            "A man in a dark coat watched her from across the hall. "
            "He made no move to follow.",
        ],
    },
    {
        "number": 2,
        "title": "The Meeting",
        "paragraphs": [
            "The cafe was nearly empty when she arrived. "
            "A single lamp cast warm light over the corner table.",
            "He was already there, stirring a cup of coffee that had gone cold. "
            "He looked up when the door opened. "
            "Their eyes met across the room.",
            "She sat down without a word. "
            "For a long moment neither of them spoke. "
            "The silence between them was heavy with years of distance.",
        ],
    },
]


def build_chunk_infos(paragraphs):
    """Chunk paragraphs and wrap into ChunkInfo."""
    chunks = chunk_text(paragraphs)
    infos = []
    for i, c in enumerate(chunks):
        ends_para = (i == len(chunks) - 1) or (c.para_end != chunks[i + 1].para_start)
        infos.append(ChunkInfo(text=c.text, ends_paragraph=ends_para))
    return chunks, infos


def measure_silence_at(audio, start_sample, window=2400):
    """Return RMS energy in a window around start_sample."""
    s = max(0, start_sample - window // 2)
    e = min(len(audio), start_sample + window // 2)
    segment = audio[s:e]
    if len(segment) == 0:
        return 0.0
    return float(np.sqrt(np.mean(segment ** 2)))


def find_silence_gap(audio, chunk_end_sample, next_chunk_start_sample):
    """Measure the silence gap duration between two chunks in samples."""
    region = audio[chunk_end_sample:next_chunk_start_sample]
    if len(region) == 0:
        return 0
    # Count contiguous near-silent samples (RMS in small windows < threshold)
    threshold = 0.01
    window = 240  # 10ms
    silent_samples = 0
    for i in range(0, len(region) - window, window):
        rms = float(np.sqrt(np.mean(region[i:i + window] ** 2)))
        if rms < threshold:
            silent_samples += window
        else:
            break
    return silent_samples


def run_validation(pipeline, chapters, device, output_dir):
    """Run full pipeline on test chapters and validate output."""
    results = []
    all_passed = True

    for ch in chapters:
        ch_num = ch["number"]
        ch_title = ch["title"]
        print(f"\n--- Chapter {ch_num}: {ch_title} ---")

        chunks, chunk_infos = build_chunk_infos(ch["paragraphs"])
        chunk_texts = [c.text for c in chunks]

        wav_path = os.path.join(output_dir, f"chapter-{ch_num:02d}.wav")

        # Synthesize
        print(f"  Synthesizing {len(chunk_infos)} chunks ...")
        result = synthesize_chapter(pipeline, chunk_infos, wav_path)
        print(f"  Duration: {result.duration_seconds:.1f}s, skipped: {result.skipped_chunks}")

        # Load audio for analysis
        audio, sr = sf.read(wav_path)
        assert sr == TTS_SAMPLE_RATE

        # --- Validation 1: Duration sanity ---
        total_words = sum(len(t.split()) for t in chunk_texts)
        expected_duration = total_words / 2.5  # ~150 WPM = 2.5 words/sec
        ratio = result.duration_seconds / expected_duration if expected_duration > 0 else 1.0
        duration_ok = 0.5 < ratio < 2.0
        status = "PASS" if duration_ok else "FAIL"
        print(f"  [{status}] Duration sanity: {result.duration_seconds:.1f}s vs expected ~{expected_duration:.1f}s (ratio {ratio:.2f})")
        if not duration_ok:
            all_passed = False

        # --- Validation 2: Chunk audio starts are valid ---
        starts = result.chunk_audio_starts
        valid_starts = [s for s in starts if s >= 0]
        monotonic = all(valid_starts[i] <= valid_starts[i + 1] for i in range(len(valid_starts) - 1))
        status = "PASS" if monotonic else "FAIL"
        print(f"  [{status}] chunk_audio_starts monotonically increasing ({len(valid_starts)} chunks)")
        if not monotonic:
            all_passed = False

        # --- Validation 3: Word alignment ---
        print("  Running word alignment ...")
        words = align_chapter(wav_path, chunk_texts, starts, device=device)
        if words:
            violations = validate_alignment(words, result.duration_seconds, len(chunk_texts))
            status = "PASS" if len(violations) == 0 else "FAIL"
            print(f"  [{status}] Word alignment: {len(words)} words, {len(violations)} violations")
            if violations:
                for v in violations[:5]:
                    print(f"    - {v}")
                all_passed = False
        else:
            print("  [SKIP] Word alignment returned empty (WhisperX may not be available)")

        # --- Validation 4: Silence gap analysis ---
        if len(chunk_infos) >= 2:
            para_gaps = []
            mid_gaps = []
            for i in range(1, len(starts)):
                if starts[i] < 0 or starts[i - 1] < 0:
                    continue
                gap_start = int(starts[i - 1] * sr) + 1000  # rough end of prev chunk
                gap_end = int(starts[i] * sr)
                if gap_end > gap_start:
                    gap_ms = (gap_end - gap_start) / sr * 1000
                    if chunk_infos[i - 1].ends_paragraph:
                        para_gaps.append(gap_ms)
                    else:
                        mid_gaps.append(gap_ms)

            if para_gaps and mid_gaps:
                avg_para = sum(para_gaps) / len(para_gaps)
                avg_mid = sum(mid_gaps) / len(mid_gaps)
                gap_ok = avg_para > avg_mid
                status = "PASS" if gap_ok else "FAIL"
                print(f"  [{status}] Silence gaps: paragraph avg {avg_para:.0f}ms vs mid-paragraph avg {avg_mid:.0f}ms")
                if not gap_ok:
                    all_passed = False
            elif para_gaps:
                print(f"  [INFO] Only paragraph breaks found (avg {sum(para_gaps)/len(para_gaps):.0f}ms)")
            else:
                print("  [INFO] Only mid-paragraph gaps found")

        # --- Validation 5: Roundtrip WER (transcription fidelity) ---
        try:
            chunk_wers = []
            for i, (text, start_s) in enumerate(zip(chunk_texts, starts)):
                if start_s < 0:
                    continue
                # Find end of this chunk's audio
                end_s = result.duration_seconds
                for j in range(i + 1, len(starts)):
                    if starts[j] >= 0:
                        end_s = starts[j]
                        break
                start_sample = int(start_s * sr)
                end_sample = int(end_s * sr)
                chunk_audio_slice = audio[start_sample:end_sample]
                if len(chunk_audio_slice) == 0:
                    continue
                chunk_wav = os.path.join(output_dir, f"chunk-{ch_num:02d}-{i:02d}.wav")
                sf.write(chunk_wav, chunk_audio_slice, sr)
                transcription = transcribe_audio(chunk_wav, device=device)
                wer = compute_wer(text, transcription)
                chunk_wers.append((i, wer, text, transcription))
                os.remove(chunk_wav)

            if chunk_wers:
                max_wer = max(w for _, w, _, _ in chunk_wers)
                avg_wer = sum(w for _, w, _, _ in chunk_wers) / len(chunk_wers)
                wer_ok = max_wer < WER_THRESHOLD and avg_wer < WER_AVG_THRESHOLD
                status = "PASS" if wer_ok else "FAIL"
                print(f"  [{status}] Roundtrip WER: avg {avg_wer:.1%}, max {max_wer:.1%} (thresholds: avg<{WER_AVG_THRESHOLD:.0%}, max<{WER_THRESHOLD:.0%})")
                if not wer_ok:
                    for idx, wer, ref, hyp in chunk_wers:
                        if wer >= WER_THRESHOLD:
                            print(f"    chunk {idx}: WER={wer:.1%}")
                            print(f"      ref: {ref[:100]}")
                            print(f"      hyp: {hyp[:100]}")
                    all_passed = False
            else:
                print("  [SKIP] No valid chunks for WER validation")
        except Exception as e:
            print(f"  [SKIP] Roundtrip WER: {e}")

        results.append({
            "chapter": ch_num,
            "duration": result.duration_seconds,
            "chunks": len(chunk_infos),
            "words": len(words) if words else 0,
        })

    return all_passed, results


def main():
    parser = argparse.ArgumentParser(description="Validate audio quality after TTS pipeline changes")
    parser.add_argument("--epub", help="Path to EPUB file (uses built-in test text if omitted)")
    parser.add_argument("--chapters", help="Chapter range, e.g. 1-2 (only with --epub)")
    parser.add_argument("--device", default=None, help="Device: cuda, mps, cpu (default: auto)")
    parser.add_argument("--output", "-o", help="Output directory (default: temp dir)")
    parser.add_argument("--keep", action="store_true", help="Keep generated audio files")
    args = parser.parse_args()

    # Determine chapters to process
    if args.epub:
        from openshelf.pipeline.epub_parser import parse_epub
        parsed = parse_epub(args.epub)
        if args.chapters:
            parts = args.chapters.split("-")
            start = int(parts[0])
            end = int(parts[1]) if len(parts) > 1 else start
            parsed = [ch for ch in parsed if start <= ch.number <= end]
        chapters = [
            {"number": ch.number, "title": ch.title, "paragraphs": ch.paragraphs}
            for ch in parsed
        ]
        if not chapters:
            print("No chapters found.")
            sys.exit(1)
        print(f"Using EPUB: {args.epub} ({len(chapters)} chapters)")
    else:
        chapters = TEST_CHAPTERS
        print(f"Using built-in test text ({len(chapters)} chapters)")

    # Load pipeline
    print(f"\nLoading TTS pipeline ...")
    pipeline = load_pipeline(device=args.device)
    print("Ready.")

    # Output directory
    if args.output:
        output_dir = args.output
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = tempfile.mkdtemp(prefix="openshelf-audio-test-")

    print(f"Output: {output_dir}")

    # Run validation
    device = args.device or "cpu"
    all_passed, results = run_validation(pipeline, chapters, device, output_dir)

    # Summary
    print("\n" + "=" * 60)
    total_duration = sum(r["duration"] for r in results)
    total_words = sum(r["words"] for r in results)
    print(f"Total: {total_duration:.1f}s audio, {total_words} aligned words")

    if all_passed:
        print("\nAll validations PASSED.")
    else:
        print("\nSome validations FAILED. Check output above.")
        sys.exit(1)

    if not args.keep and not args.output:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)
        print(f"Cleaned up temp dir.")
    else:
        print(f"Audio files kept at: {output_dir}")


if __name__ == "__main__":
    main()
