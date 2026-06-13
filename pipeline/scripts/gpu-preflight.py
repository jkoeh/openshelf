#!/usr/bin/env python3
"""Check GPU readiness for OpenShelf TTS engines."""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openshelf.pipeline.gpu_preflight import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
