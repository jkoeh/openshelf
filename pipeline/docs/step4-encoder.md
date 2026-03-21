# Step 4: Encoder

**Module:** `src/openshelf/pipeline/encoder.py`
**Test:** `tests/pipeline/test_encoder.py`

## Purpose

Convert a WAV file to Opus format using ffmpeg. Returns the audio duration. Opus at 48kbps provides good speech quality at roughly 1/3 the size of MP3 128kbps.

```mermaid
graph TD
    A[WAV file] --> B[soundfile.info]
    B --> C{Sample rate matches TTS_SAMPLE_RATE?}
    C -->|No| D[Log warning]
    C -->|Yes| E[Continue]
    D --> E
    E --> F[ffmpeg -c:a libopus -b:a 48k]
    F --> G[Opus file]
    G --> H{delete_wav?}
    H -->|Yes| I[os.remove WAV]
    H -->|No| J[Keep WAV]
    I --> K[Return duration seconds]
    J --> K
```

## Interface

### Public Functions

```python
def audio_duration(path: str) -> float
    # Get duration of any audio file via ffprobe (WAV, Opus, etc.)
    # Returns seconds as float

def encode_to_opus(
    wav_path: str,
    opus_path: str,
    bitrate: str = OPUS_BITRATE,   # "48k"
    delete_wav: bool = True,
) -> float
    # Returns duration in seconds (from WAV sample count, not ffprobe)
```

## Behavior

### Encoding

Runs ffmpeg as a subprocess: `ffmpeg -i <wav> -c:a libopus -b:a 48k -y <opus>`

The `-y` flag overwrites existing output (safe because the caller checks for existence before calling).

### Duration Calculation

Duration is calculated from the WAV's frame count divided by sample rate (`soundfile.info`). This is exact — no ffprobe rounding.

### Sample Rate Check

If the WAV sample rate doesn't match `TTS_SAMPLE_RATE` (24000 Hz), a warning is logged but encoding proceeds. ffmpeg handles resampling transparently.

### Output Directory

Parent directories of `opus_path` are created with `os.makedirs(exist_ok=True)`.

### WAV Cleanup

By default, the source WAV is deleted after successful Opus encoding. Pass `delete_wav=False` to keep it (useful for debugging or re-encoding).

### Error Propagation

- WAV file doesn't exist: `FileNotFoundError` from `soundfile.info`
- ffmpeg fails: `subprocess.CalledProcessError` from `subprocess.run(check=True)`
- Both propagate to the caller — no silent swallowing

## Dependencies

- `ffmpeg` — system binary (must be installed with libopus support)
- `soundfile` — WAV metadata reading
- Config: `OPUS_BITRATE` ("48k"), `TTS_SAMPLE_RATE` (24000)
