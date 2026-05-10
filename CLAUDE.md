# CLAUDE.md

## What This Project Is

**OpenShelf** is an open source public domain audiobook platform. It downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio using Kokoro TTS with word-level timestamps extracted directly from Kokoro's token output, and serves them globally via Cloudflare R2.

## End-to-End Flow

The pipeline produces audio + word-aligned text; R2 stores it; the worker serves it; the client streams audio while highlighting the current word.

```mermaid
flowchart LR
    subgraph Pipeline[Pipeline — Python]
        direction TB
        P1[EPUB] --> P2[parse_epub<br/>chapters + ContentElements]
        P2 --> P3[text_chunker<br/>ChunkInfo per chunk]
        P3 --> P4[tts.synthesize_chapter<br/>Kokoro KPipeline]
        P4 -->|WAV + chunk_words<br/>+ chunk_audio_starts| P5[encode_to_aac<br/>m4a]
        P4 --> P6[chapter_data.json<br/>chunks + words]
        P5 --> P7[rendition-manifest.json<br/>per-build chapter list]
        P2 --> P8[annotated EPUB<br/>+ cover]
        P9[compute_build_id<br/>7-char hash of pipeline + config + voice] --> P5
        P9 --> P6
        P9 --> P7
        P7 --> P10[book manifest.json<br/>renditions[].current_build]
    end

    subgraph R2[Cloudflare R2]
        direction TB
        R_EPUB[book.epub]
        R_COVER[cover jpg/png]
        R_BOOKMAN[manifest.json<br/>book-level pointer · MUTABLE]
        R_RMAN[audio/{rendition}/builds/{build}/<br/>rendition-manifest.json]
        R_M4A[audio/{rendition}/builds/{build}/<br/>chapter-NN.m4a]
        R_CD[audio/{rendition}/builds/{build}/<br/>chapter_data.json]
        R_CAT[catalog.json]
    end

    P5 --> R_M4A
    P6 --> R_CD
    P7 --> R_RMAN
    P10 --> R_BOOKMAN
    P8 --> R_EPUB
    P8 --> R_COVER

    subgraph Worker[Worker — Cloudflare/Hono + zod-openapi]
        direction TB
        W_CAT[GET /catalog]
        W_BOOK[GET /books/:a/:t<br/>→ book manifest with current_build per rendition]
        W_CH[GET /books/:a/:t/chapters/:n<br/>?rendition · ?build<br/>→ text + flat words[chunk_idx]]
        W_AUDIO[GET /books/:a/:t/audio/:n<br/>?rendition · ?build<br/>→ m4a stream / range]
        W_COVER[GET /books/:a/:t/cover]
        W_EPUB[GET /books/:a/:t/epub]
        W_SPEC[GET /openapi.json + /docs<br/>auto-generated from Zod schemas]
    end

    R_CAT --> W_CAT
    R_BOOKMAN --> W_BOOK
    R_CD  --> W_CH
    R_M4A --> W_AUDIO
    R_COVER --> W_COVER
    R_EPUB --> W_EPUB

    subgraph Client[Client — Expo]
        direction TB
        C1[Catalog page<br/>fetchCatalog]
        C2[Book detail<br/>fetchBook → manifest with renditions]
        C3[Reader page<br/>pin build at chapter load<br/>fetchChapter rendition build → text + words]
        C3 --> C4[expo-audio player<br/>streams m4a rendition build]
        C3 --> C5[useSyncEngine<br/>rAF reads player.currentTime]
        C5 -->|findWordAtTime| C6[active word / chunk]
        C6 --> C7[ReadingPane highlights word]
        C6 -.tap word.-> C4
    end

    W_CAT  --> C1
    W_BOOK --> C2
    W_CH   --> C3
    W_AUDIO --> C4
```



Notes:

- The `chapter_data.json` produced in step P6 is the single source for both chunk text and word timestamps the client needs — there is no separate alignment fetch on the happy path.
- The client does not poll status for sync; `useSyncEngine` reads `player.currentTime` directly inside `requestAnimationFrame` and only re-renders when the active word index changes.
- Tap-to-seek in the reader looks up `words[i].start` and calls `player.seekTo`.

### Rendition vs build invariant

OpenShelf separates two orthogonal concepts that used to be conflated:

- **Rendition** is a user-facing artistic identity (a Kokoro voice + engine). Examples: `kokoro-af-heart`, `kokoro-bf-emma`. Stable across pipeline changes. The user picks a rendition.
- **Build** is an internal pipeline-output identity. A 7-char content hash derived from pipeline-affecting config + `PIPELINE_VERSION`. Bumps every time output bytes change. The user never sees it.

Storage and HTTP URLs include **both**: `audio/{rendition}/builds/{build}/...` on R2; `?rendition=...&build=...` on every immutable HTTP route. The book-level `manifest.json` is the single mutable pointer that names the `current_build` per rendition. This makes audio + chapter_data + rendition-manifest a coherent atomic snapshot per (rendition, build), so a client can never mix bytes from different builds mid-session.

This is the contract that lets every per-build URL set `Cache-Control: immutable` honestly. Only the book manifest carries a short cache.

## Monorepo Structure

```
pipeline/               # Python — EPUB ingestion, TTS, R2 upload
  src/openshelf/        # Python package
  scripts/              # CLI entry points
  tests/                # Python tests (mocked, offline)
  docs/                 # Pipeline step documentation
  requirements.txt
  pyproject.toml

worker/                 # TypeScript — Cloudflare Worker API
  src/                  # Hono routes
  tests/                # Vitest + miniflare tests
  package.json
  wrangler.toml

client/                 # TypeScript — Expo app (web + iOS + Android)
  app/                  # Expo Router file-based routes
  components/           # Reusable UI components
  lib/                  # Business logic (sync engine, API, storage)
  package.json
  app.json

download/               # (gitignored) downloaded EPUBs
audio/                  # (gitignored) generated audio files
plans/                  # design docs and plans
```

## Root Commands

```bash
npm run dev          # Start worker + client concurrently
npm run test         # Run worker + client tests
npm run typecheck    # Type-check worker + client
```

## Conventions

- Each component owns its dependency file (`pipeline/requirements.txt`, `worker/package.json`, `client/package.json`)
- Shared data contract: pipeline writes JSON (manifest, chapter_data) to R2; worker reads it. `chapter_data.json` carries per-chapter chunk text and inline Kokoro word timestamps in a single file.
- `sanitize()` in `pipeline/src/openshelf/scrapers/http.py` is the single source of truth for slug generation
- Idempotent at every level: file exists -> skip, R2 key exists -> skip

## Docs-First Workflow (mandatory)

Documentation in this repo is treated as the **spec**, not a trailing description of code. Every change follows this order:

1. **Identify the spec.** Locate every doc that describes the behavior you're about to change. The relevant files are:
  - `CLAUDE.md` (this file — end-to-end flow, data contracts, cross-cutting rules)
  - The component's `CLAUDE.md` (`pipeline/`, `worker/`, `client/`)
  - The relevant pipeline step doc under `pipeline/docs/step*.md`
  - The end-to-end mermaid flow above (if data shape, R2 layout, or routes change)
  - Inline interface blocks (dataclasses, route shapes, R2 key layout) inside those docs
2. **Update the docs first.** Edit the docs and mermaid flow to describe the **target** behavior. Pipeline data shape, R2 keys, worker route response shape, and client hook signatures must match what the code will be after the change.
3. **Implement against the updated docs.** Write code, types, and tests so they conform to what the doc now says. The doc is the contract; the code mirrors it.
4. **Verify alignment before committing.** Re-read the relevant doc sections and confirm the diff matches. Run typecheck + tests.

### Worker contract is machine-generated

The worker's request/response shapes are **not** described in prose. Each route is a Zod schema in `worker/src/routes/*.ts`; `@hono/zod-openapi` synthesizes `/api/v1/openapi.json` from those schemas at request time. To change a route's shape, edit the Zod schema — the type system, runtime validation, and the OpenAPI doc all update together. Treat `/api/v1/openapi.json` (and `/api/v1/docs`) as the worker's spec; this CLAUDE.md and `worker/CLAUDE.md` only describe **why** routes exist, not their wire shape. See `worker/docs/openapi.md` for the authoring pattern.

### Out-of-sync detection — STOP and ask

Before editing anything in a region (a module, a route, a hook, an R2 key layout), spot-check that the existing doc agrees with the existing code. If you find a mismatch — e.g., the doc claims a function returns X but the code returns Y, or a route is documented but missing, or an R2 key listed in `step6-r2.md` is no longer written by the pipeline — **do not silently "fix" the doc and proceed**. Flag it to the user and ask which side is correct. Drift usually means in-flight work, an aborted refactor, or a bug, and resolving it incorrectly can erase intent.

A change is only complete when docs, code, and tests all describe the same system.

## Do NOT

- Over-engineer — no abstractions until there are 2+ concrete uses
- Create files outside the established structure without updating this doc or the component's CLAUDE.md
- Edit code before updating the spec docs above. If the docs are wrong or missing, fix the docs first.
- Silently reconcile a docs/code mismatch — surface it and ask.

