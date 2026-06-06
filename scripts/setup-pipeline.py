#!/usr/bin/env python3
"""Install the Python pipeline deps with the right torch wheel, then verify it.

uv's ``--torch-backend=auto`` probes the local CUDA driver and selects the
matching torch wheel (CUDA on an NVIDIA box, CPU/MPS elsewhere), so this one
script is correct on a GPU workstation, a Mac, and CI alike.

Usage:
    uv run python scripts/setup-pipeline.py           # install + verify
    uv run python scripts/setup-pipeline.py --verify   # verify only
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO_ROOT / "pipeline" / "requirements.txt"


def install() -> None:
    print("Installing pipeline deps (torch wheel auto-selected by uv) ...")
    # --torch-backend=auto probes the local CUDA driver and picks the matching
    # wheel index. torch + torchaudio are force-reinstalled because an already
    # satisfied CPU build (e.g. 2.8.0) otherwise looks "good enough" to uv and
    # the CUDA swap is skipped; both move in lockstep to keep versions matched.
    subprocess.run(
        [
            "uv", "pip", "install",
            "-r", str(REQUIREMENTS),
            "--torch-backend=auto",
            "--reinstall-package", "torch",
            "--reinstall-package", "torchaudio",
        ],
        check=True,
    )


def verify() -> None:
    import torch

    print(f"torch {torch.__version__}")
    cuda = torch.cuda.is_available()
    print(f"cuda available {cuda}")
    print(f"device {torch.cuda.get_device_name(0) if cuda else 'cpu'}")


def main() -> int:
    verify_only = "--verify" in sys.argv[1:]
    try:
        if not verify_only:
            install()
        verify()
    except subprocess.CalledProcessError as exc:
        print(f"install failed (exit {exc.returncode})", file=sys.stderr)
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
