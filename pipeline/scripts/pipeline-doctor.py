#!/usr/bin/env python3
"""Inspect a local OpenShelf build directory."""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.pipeline_doctor import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
