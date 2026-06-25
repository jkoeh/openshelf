# Pipeline AGENTS.md

## Scope

These instructions apply to the Python pipeline under `pipeline/`.

OpenShelf's pipeline converts semantic EPUB sections into directed TTS audio, aligns the
final audio with WhisperX word timestamps, encodes AAC `.m4a` files, and writes
the immutable per-build artifacts uploaded to R2.

## Specs To Read First

Documentation is the spec. Before changing pipeline behavior, update and read
the relevant docs first:

- `pipeline/docs/step1-epub-parser.md`
- `pipeline/docs/step0-run-context.md`
- `pipeline/docs/step2-text-chunker.md`
- `pipeline/docs/step2b-voice-director.md`
- `pipeline/docs/step3-tts.md`
- `pipeline/docs/engine-knowledge-base.md`
- `pipeline/docs/step4-encoder.md`
- `pipeline/docs/step5a-manifest.md`
- `pipeline/docs/step5c-rendition-manifest.md`
- `pipeline/docs/step6-r2.md`
- `pipeline/docs/dag-cli.md`
- `pipeline/docs/ops-tools.md`

For any TTS engine work, start with
`pipeline/docs/engine-knowledge-base.md`, then open only the relevant adapter
under `pipeline/src/openshelf/pipeline/engines/`.

## Engine Rules

- `TTSEngine` in `pipeline/src/openshelf/pipeline/tts_engine.py` is the shared adapter contract.
- Kokoro, F5-TTS, and Chatterbox adapters must preserve the same version-2
  public output: `.m4a` audio plus `section_data.json` with separate heading
  metadata, original body text, and WhisperX word timestamps.
- `voice_direction.json` is audit metadata. It may include synthesis-only text, emotion labels, pace, pauses, and engine-specific control decisions. It must not replace reader text.
- `run.json` is the per-build resume contract. Any resumability change must keep it aligned with `pipeline/docs/step0-run-context.md`.
- WhisperX is the canonical final sync source for every current engine.
- Engine-native timestamps, prompt markers, and paralinguistic tags must not be
  serialized to `section_data.json`.
- Headings and generated credits are deterministic narrator regions. They
  bypass character attribution and performance-direction LLM calls and must
  not be merged into body chunks.

## CLI Rules

- `openshelf-pipeline` is the canonical command surface.
- `openshelf-pipeline books ...` owns user-facing book workflows: search,
  download, process local EPUBs, upload, and catalog refresh.
- `openshelf-pipeline dag ...` owns repairable artifact stages and full DAG
  runs for explicit EPUB/build paths.
- `openshelf-pipeline ops ...`, `voices ...`, `qa ...`, and `profile ...` own
  local diagnostics, reference-voice prep, quality checks, and profiling.
- Deleted legacy script filenames are not preserved.

## Tests

Pipeline tests are Python `unittest` tests under `pipeline/tests/` and should
be mocked/offline. Engine tests should not require real model downloads, GPU,
network, R2, or ffmpeg.

Useful focused commands from the repo root:

```bash
python -m unittest pipeline.tests.pipeline.test_tts
python -m unittest pipeline.tests.pipeline.test_engines_kokoro
python -m unittest pipeline.tests.pipeline.test_engines_f5tts
python -m unittest pipeline.tests.pipeline.test_audio_director
python -m unittest pipeline.tests.pipeline.test_word_aligner_protocol
```
