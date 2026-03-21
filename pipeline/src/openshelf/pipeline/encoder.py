"""Step 4: Convert WAV audio to Opus."""

import logging
import os
import subprocess

import soundfile as sf

from openshelf.config import OPUS_BITRATE, TTS_SAMPLE_RATE

logger = logging.getLogger(__name__)


def audio_duration(path: str) -> float:
    """Get duration of an audio file via ffprobe. Works with WAV, Opus, etc."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def encode_to_opus(
    wav_path: str,
    opus_path: str,
    bitrate: str = OPUS_BITRATE,
    delete_wav: bool = True,
) -> float:
    info = sf.info(wav_path)

    if info.samplerate != TTS_SAMPLE_RATE:
        logger.warning(
            "WAV sample rate %d != expected %d: %s",
            info.samplerate, TTS_SAMPLE_RATE, wav_path,
        )

    os.makedirs(os.path.dirname(opus_path), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-i", wav_path, "-c:a", "libopus", "-b:a", bitrate, "-y", opus_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    duration = info.frames / info.samplerate

    if delete_wav:
        os.remove(wav_path)

    return duration
