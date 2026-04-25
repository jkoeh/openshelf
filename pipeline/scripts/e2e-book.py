#!/usr/bin/env python3
"""Run e2e download + conversion + upload for one book.

Usage:
    python3 pipeline/scripts/e2e-book.py Kafka Metamorphosis
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run process-books.py e2e with upload enabled"
    )
    parser.add_argument("author", help="Author name, e.g. Kafka")
    parser.add_argument("title", help="Book title, e.g. Metamorphosis")
    args = parser.parse_args()

    cmd = [
        "uv",
        "run",
        "--python",
        "3.11",
        "--with-requirements",
        "pipeline/requirements.txt",
        "--with",
        "python-dotenv",
        "python",
        "pipeline/scripts/process-books.py",
        "--author",
        args.author,
        "--book",
        args.title,
        "--upload",
    ]

    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
