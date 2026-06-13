#!/usr/bin/env python3
"""Compatibility wrapper for the DAG full-book runner.

The implementation lives in ``openshelf.pipeline.dag_cli.run_book``. Keep this
script so existing local commands continue to work while ``process-books.py``
and new automation use the DAG runner directly.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running from the scripts/ directory without pip install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.dag_cli import (  # noqa: E402
    _chapter_direction_path,
    _direction_speed_summary,
    _parse_chapter_filter,
    add_run_arguments,
    run_book,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert EPUB to AAC audiobook")
    add_run_arguments(parser, positional_epub=True)
    args = parser.parse_args()
    try:
        run_book(args)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
