# CLAUDE.md

## What This Project Is

**OpenShelf** is an open source public domain audiobook platform. It downloads EPUB books from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio using Kokoro TTS with word-level alignment via WhisperX, and serves them globally via Cloudflare R2.

## Monorepo Structure

```
pipeline/               # Python — EPUB ingestion, TTS, alignment, R2 upload
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
- Shared data contract: pipeline writes JSON (manifest, chunks, word_alignment) to R2; worker reads it
- `sanitize()` in `pipeline/src/openshelf/scrapers/http.py` is the single source of truth for slug generation
- Idempotent at every level: file exists -> skip, R2 key exists -> skip

## Do NOT

- Over-engineer — no abstractions until there are 2+ concrete uses
- Create files outside the established structure without updating this doc or the component's CLAUDE.md
