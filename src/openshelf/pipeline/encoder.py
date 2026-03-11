"""Step 4: Convert WAV audio to MP3."""

import logging
import os
import subprocess

import soundfile as sf

from openshelf.config import MP3_BITRATE, TTS_SAMPLE_RATE

logger = logging.getLogger(__name__)


def encode_to_mp3(
    wav_path: str,
    mp3_path: str,
    bitrate: str = MP3_BITRATE,
    delete_wav: bool = True,
) -> float:
    info = sf.info(wav_path)

    if info.samplerate != TTS_SAMPLE_RATE:
        logger.warning(
            "WAV sample rate %d != expected %d: %s",
            info.samplerate, TTS_SAMPLE_RATE, wav_path,
        )

    os.makedirs(os.path.dirname(mp3_path), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-i", wav_path, "-b:a", bitrate, "-y", mp3_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    duration = info.frames / info.samplerate

    if delete_wav:
        os.remove(wav_path)

    return duration
