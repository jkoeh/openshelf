"""Shared configuration and constants."""

import os
from pathlib import Path

# Project root — two levels up from this file (src/openshelf/config.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Default paths
DEFAULT_INPUT_DIR = PROJECT_ROOT / "download" / "books"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "audio"

# Scraper
USER_AGENT = "BookScraper/1.0 (educational project)"
GUTENDEX_API = "https://gutendex.com/books"
SE_BASE = "https://standardebooks.org"

# TTS
TTS_VOICE = "af_heart"
TTS_LANGUAGE = "a"  # Kokoro lang_code: a=American English, b=British English
TTS_SAMPLE_RATE = 24000
CHUNK_MAX_WORDS = 450
SILENCE_BETWEEN_CHUNKS_MS = 400

# Encoder
MP3_BITRATE = "128k"

# R2
R2_BUCKET = os.getenv("R2_BUCKET", "openshelf-audio")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
