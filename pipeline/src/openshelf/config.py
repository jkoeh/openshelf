"""Shared configuration and constants."""

import os
from pathlib import Path

# Project root — three levels up from this file (pipeline/src/openshelf/config.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Load .env files if present. Values already exported in the shell win.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / "pipeline" / ".env")
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# Default paths
DEFAULT_INPUT_DIR = PROJECT_ROOT / "download" / "books"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "audio"

# Scraper
USER_AGENT = "BookScraper/1.0 (educational project)"
GUTENDEX_API = "https://gutendex.com/books"
SE_BASE = "https://standardebooks.org"

# Pipeline version — bumped manually when code changes affect TTS output bytes
# but config constants below are unchanged. Combined with config + voice + rendition
# into the build_id hash. See pipeline/docs/step6-r2.md.
PIPELINE_VERSION = "1"

# TTS
TTS_VOICE = "af_heart"
TTS_ENGINE = os.getenv("TTS_ENGINE", "kokoro")
TTS_LANGUAGE = "a"  # Kokoro lang_code: a=American English, b=British English
TTS_SAMPLE_RATE = 24000
CHUNK_MAX_WORDS = 200
SILENCE_PARAGRAPH_BREAK_MS = 400
SILENCE_MID_PARAGRAPH_MS = 90
SILENCE_INTERNAL_PARAGRAPH_BREAK_MS = 300
CROSSFADE_MS = 15
LEAD_IN_SILENCE_MS = 50
VOICES_DIR = PROJECT_ROOT / "pipeline" / "voices"

# LLM voice direction
CAST_MODE = os.getenv("CAST_MODE", "solo").strip().lower()
PERFORMANCE_DIRECTION_MODE = os.getenv("PERFORMANCE_DIRECTION_MODE", "batched").strip().lower()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv(
    "ANTHROPIC_MODEL",
    LLM_MODEL or "claude-haiku-4-5-20251001",
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", LLM_MODEL or "gpt-5-nano")
OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "4096"))
REGISTRY_OPENING_CHARS = 4000
EMBEDDED_DIALOGUE_MODE = os.getenv("EMBEDDED_DIALOGUE_MODE", "reciter")

# WER validation
WER_THRESHOLD = 0.30
WER_AVG_THRESHOLD = 0.15
WHISPER_MODEL_SIZE = "base"

# Encoder
AAC_BITRATE = "48k"

# R2
R2_BUCKET = os.getenv("R2_BUCKET", "openshelf")
R2_PREFIX_BOOKS = "books"
R2_DEFAULT_RENDITION = "kokoro-af-heart"
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")

# R2 cache headers
R2_CACHE_CONTROL_IMMUTABLE = "public, max-age=31536000, immutable"
R2_CACHE_CONTROL_MANIFEST = "public, max-age=60"
