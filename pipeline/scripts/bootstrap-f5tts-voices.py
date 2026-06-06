#!/usr/bin/env python3
"""Generate local F5-TTS reference clips from Kokoro preset voices."""

from __future__ import annotations

import argparse
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.engines.f5tts import bootstrap_kokoro_reference_voices  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render pipeline/voices/f5tts/*.wav reference clips from Kokoro presets.",
    )
    parser.add_argument(
        "--voices",
        nargs="*",
        default=None,
        help="Optional Kokoro presets or F5 IDs to render, e.g. af_heart f5tts-bm_george.",
    )
    parser.add_argument(
        "--voices-dir",
        default=None,
        help="Base voices directory. Defaults to pipeline/voices.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Optional Kokoro device override, e.g. cpu, cuda, or mps.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate clips that already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned files without loading Kokoro or writing WAVs.",
    )
    args = parser.parse_args()

    results = bootstrap_kokoro_reference_voices(
        voices_dir=args.voices_dir,
        voice_ids=args.voices,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        device=args.device,
    )

    for result in results:
        print(f"{result.status:15} {result.voice_id:18} {result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
