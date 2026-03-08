"""Step 4: Convert WAV audio to MP3."""

import logging
import os

from pydub import AudioSegment

from openshelf.config import MP3_BITRATE, TTS_SAMPLE_RATE

logger = logging.getLogger(__name__)


def encode_to_mp3(
    wav_path: str,
    mp3_path: str,
    bitrate: str = MP3_BITRATE,
    delete_wav: bool = True,
) -> float:
    segment = AudioSegment.from_wav(wav_path)

    if segment.frame_rate != TTS_SAMPLE_RATE:
        logger.warning(
            "WAV sample rate %d != expected %d: %s",
            segment.frame_rate, TTS_SAMPLE_RATE, wav_path,
        )

    os.makedirs(os.path.dirname(mp3_path), exist_ok=True)
    segment.export(mp3_path, format="mp3", bitrate=bitrate)

    duration = len(segment) / 1000.0

    if delete_wav:
        os.remove(wav_path)

    return duration
