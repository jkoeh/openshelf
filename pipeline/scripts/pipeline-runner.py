#!/usr/bin/env python3
"""Run process-books.py with GPU preflight and optional background logs."""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.pipeline_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
