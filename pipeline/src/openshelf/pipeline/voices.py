"""Generate local reference clips from Kokoro preset voices."""

from __future__ import annotations

import argparse
from typing import Sequence

from openshelf.pipeline.engines.chatterbox import (
    bootstrap_kokoro_reference_voices as bootstrap_chatterbox_reference_voices,
)
from openshelf.pipeline.engines.f5tts import (
    bootstrap_kokoro_reference_voices as bootstrap_f5tts_reference_voices,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openshelf-pipeline voices bootstrap",
        description="Render pipeline/voices/<engine>/*.wav reference clips from Kokoro presets.",
    )
    parser.add_argument(
        "--engine",
        choices=["f5tts", "chatterbox"],
        default="f5tts",
        help="Reference-voice target engine. Defaults to f5tts for backward compatibility.",
    )
    parser.add_argument(
        "--voices",
        nargs="*",
        default=None,
        help=(
            "Optional Kokoro presets or engine IDs to render, e.g. af_heart, "
            "f5tts-bm_george, or chatterbox-bf_alice."
        ),
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
    args = parser.parse_args(argv)

    bootstrap = (
        bootstrap_chatterbox_reference_voices
        if args.engine == "chatterbox"
        else bootstrap_f5tts_reference_voices
    )
    results = bootstrap(
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
