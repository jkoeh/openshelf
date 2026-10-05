# Worker — CLAUDE.md

## What This Is

Cloudflare Worker API that serves audiobook data from R2. Built with Hono and TypeScript. Routes are defined with `@hono/zod-openapi`, so request/response shapes, runtime validation, TypeScript types, and the OpenAPI 3.1 spec all derive from a single Zod schema per route.

## API Contract

The worker's contract is **machine-generated, not hand-maintained**:

- `GET /api/v1/openapi.json` — full OpenAPI 3.1 document, synthesized at request time from the Zod schemas attached to each route
- `GET /api/v1/docs` — Swagger UI rendering of the spec

Treat `/api/v1/openapi.json` as the source of truth for any client (web, mobile, agent) that needs to know the API shape. `worker/CLAUDE.md` and the root mermaid flow describe **why** routes exist; the spec describes **what** they accept and return.

## Structure

```
src/
  index.ts              # OpenAPIHono app: route mounting, /openapi.json, /docs, error handling
  types.ts              # Env bindings, shared types
  constants.ts          # R2/cache/catalog constants
  middleware/
    cors.ts             # CORS middleware
  schemas/
    error.ts            # ErrorSchema — { error: { code, message } }
    params.ts           # SlugSchema, ChapterNumberStringSchema (shared path params)
  routes/               # one OpenAPIHono subapp per file; each defines route(s) via createRoute
    health.ts           # GET /api/v1/health
    catalog.ts          # GET /api/v1/catalog — catalog.json fast path, manifest-derived fallback
    book.ts             # GET /api/v1/books/:author/:title
    builds.ts           # GET /api/v1/books/:author/:title/builds — retained build selection metadata
    sections.ts         # GET /api/v1/books/:author/:title/sections/:sequence — text + word timestamps
    audio.ts            # GET /api/v1/books/:author/:title/sections/:sequence/audio — m4a Range stream
    source-books.ts     # GET /api/v1/source-books and internal source sync
    generation-jobs.ts  # Owner job control and PC lease protocol
    admin.ts            # Google owner identity check for browser controls
    cover.ts            # GET /api/v1/books/:author/:title/cover
    epub.ts             # GET /api/v1/books/:author/:title/epub
  utils/
    openapi-app.ts      # createOpenAPIApp() — OpenAPIHono factory with shared defaultHook for 400s
    r2-keys.ts          # R2 key builders
    response.ts         # Error response helpers (used by global onError/notFound)
    validation.ts       # Param regexes (kept; lifted into schemas/params.ts for routes)

docs/
  openapi.md            # How to add/modify routes — read before editing routes/

tests/
  routes/               # Vitest + @cloudflare/vitest-pool-workers tests
```

## Stack

- TypeScript, Hono v4 + `@hono/zod-openapi` v0.18, `@hono/swagger-ui` v0.5
- Zod v3 (pinned; v4 is incompatible with `@hono/zod-openapi` v0.18)
- Cloudflare Workers runtime
- R2 bucket binding (`R2_BUCKET`); optional D1 job/index binding (`JOB_DB`) and
  Worker search/auth rate-limit bindings. Job features return 503 when D1 or
  server-side secrets are absent; production must provision these before use.
- Vitest + miniflare for testing
- Biome for linting/formatting

## Commands

All commands run from the **worker/** directory.

```bash
# Dev server
npm run dev
npm run dev:local
npm run dev:remote-r2

# Deploy
npm run deploy:staging
npm run deploy:production

# Type check
npm run typecheck

# Lint + format
npm run check
npm run check:fix

# Tests
npm test

# Seed local R2
npm run seed
```

`npm run dev:local` runs local Worker code with local simulated R2. `npm run dev:remote-r2` runs local Worker code with the `R2_BUCKET` binding connected to the real `openshelf` R2 bucket via Wrangler remote bindings, so it requires `wrangler login`.

## Conventions

- Biome enforced: tabs, 100 char line width, LF line endings
- Routes are thin — extract params, fetch from R2, return JSON
- Each route is defined by a Zod-driven `createRoute(...)` block + a handler. The Zod schema is the contract: validation, types, and the OpenAPI spec all flow from it. **Never** hand-edit `openapi.json`; change the schema instead.
- All R2 key construction goes through `utils/r2-keys.ts`
- Inside `app.openapi(...)` handlers, return errors with inline `c.json({ error: { code, message } }, status)` so the response is type-checked against `ErrorSchema`. The helpers in `utils/response.ts` are reserved for the global `onError`/`notFound` (which run outside any `createRoute`).
- Path/query schemas live in `schemas/params.ts` if shared across routes; route-local response shapes live in the route file.
- Tests use `@cloudflare/vitest-pool-workers` with fixture data in `fixtures/`. They use `app.request(...)` and are unaffected by the OpenAPI migration.
- `sections.ts` reads `audio/{rendition}/builds/{build}/section_data.json` (single source of truth for heading, body text and word timestamps).

## Search and generation v1

Gutenberg-only source suggestions come from a bounded, indexed D1 table. The
PC can fill that table from Gutenberg's weekly CSV feed in capped batches.
The PC checks catalog candidates against Gutenberg's rights-bearing RDF
archive before syncing; the Worker still validates source IDs and URL hosts.
Search uses the longest typed query token as the indexed prefix at three or more
characters, then tries indexed adjacent-transposition candidates, then a
two-character indexed sample; each lookup is limited to 80 candidates. The
visitor can request a fixed Kokoro `af_heart` job with an exact `gutenberg:<id>`
source ID under a dedicated create rate limit. The owner may instead request
`expressive`, a fixed Chatterbox `af_heart` rendition whose PC pipeline uses
batched OpenAI emotion direction; public callers cannot select that mode. The
request accepts no model, key, prompt, or arbitrary engine setting. The mode is
stored with the job and returned by status and PC claim so retries preserve it.
Both modes share the existing daily-start and queue caps. Source suggestions report
publication availability and the relevant job mode, state, ID, and update time
separately, so regeneration progress or failure never hides the playable book.
The job lookup prefers an active job (including a retried older job) through
the partial `one_active_generation` index, then uses `latest_source_job` to
find the newest created job without sorting job history. Both lookups run only
for indexed, bounded token candidates. Owner authentication is
required for retry, regeneration, and cancellation. Browser administration
also accepts a Google Identity Services ID token after the Worker verifies its Google
signature, issuer, configured OAuth client audience, expiry, verified email,
and exact `johnkoeh@gmail.com` address. An unconfigured Google client ID fails
closed; the existing owner token remains valid for local CLI administration.
`GET /api/v1/admin/me` verifies Google identity before the browser exposes
controls; local owner tokens do not authenticate this browser identity route.
The OpenAI key stays on the PC and never enters a Worker binding. A separate PC
credential synchronizes source metadata and claims a
job with a renewable lease. The PC downloads only allowlisted Gutenberg EPUB
URLs and runs the exact-EPUB pipeline for the job's fixed rendition. The
consumer runs OpenAI direction only for expressive jobs and will not claim them
without a locally configured OpenAI key. Completion checks the R2 book
pointer, selected rendition manifest and every listed section audio object. Public
search is rate-limited before a bounded, 15-second in-isolate cache for
identical origin, normalized query, and limit triples. Only successful public suggestion
responses enter the cache. Isolates do not share memory, so cold instances
still query D1; clients receive `no-store`, and job state can lag by at most
15 seconds. D1 enforces active-job
deduplication, three pending jobs, two successful start reservations per UTC
day and three attempts per job. Canceling a queued/running job moves it to a
terminal `canceled` state and revokes its lease without refunding the start;
the PC stops at its next rejected heartbeat.
The local Windows monitor uses owner authentication for a bounded, no-store
queue view: all active jobs (the queue cap bounds these) plus the 20 most recent
terminal jobs, joined to source title/author. It receives priority, attempts,
timestamps, and lease expiry, but never lease tokens, build IDs, EPUB URLs, or
credentials. A running job with an expired lease is shown as stuck. Owner-only
priority changes are allowed only while queued, with normal/high values; claim
selects high-priority queued jobs before normal queued jobs, FIFO within each
level. Running jobs are never preempted. The existing owner cancel route is
the monitor's kill action; it revokes the lease and does not refund a start.
The list is rate-limited before its D1 reads, and priority updates are bounded
and authenticated before mutation.
Google-token verification is rate-limited before signature verification to bound
remote key fetches; valid local owner and PC credentials stay usable. Invalid
local owner and PC credentials also count against the authentication limit.
Rejected queue-full or
duplicate submissions consume no daily reservation. Public job status uses
the search rate limiter before D1. Public job responses omit internal build
IDs, attempt counts, and arbitrary PC error codes. All job responses are
`no-store`. Neither credential is sent in a public bundle.

## `GET /books/:author/:title` response shape

The book route returns a merge of two R2 reads: the small mutable `manifest.json` at the book root, and the per-build `rendition-manifest.json` for each rendition's `current_build`. The client sees a single response with everything needed for the book-detail page:

```json
{
  "title": "The Metamorphosis",
  "author": "Franz Kafka",
  "source": "gutenberg",
  "renditions": {
    "kokoro-af-heart": {
      "voice": "af_heart",
      "engine": "kokoro",
      "display": "Heart",
      "current_build": "2a4f9c1b3d8e7f60",
      "available_builds": ["2a4f9c1b3d8e7f60", "7e8b4d2a9c0e1234"],
      "total_duration_seconds": 1847.3,
      "chapters": [
        {"number": 1, "title": "I", "filename": "chapter-01.m4a",
         "duration_seconds": 1847.3, "word_count": 3241}
      ]
    }
  }
}
```

`total_duration_seconds` and `chapters` are sourced from the rendition-manifest of `current_build`. The on-R2 book manifest does not store them — keeping it tiny and stable across reprocesses. The merged response is short-cached as a whole (the book manifest portion is the mutable part); a freshly published build propagates within the manifest cache window.

## `GET /books/:author/:title/builds` response shape

The build-selection route is an optional discovery endpoint for clients that want to expose non-current retained builds. It reads the root book manifest, then fetches each build listed in every rendition's `available_builds` from that build's `rendition-manifest.json`. `uploaded_at` comes from the R2 object's upload timestamp for that `rendition-manifest.json`, not from the immutable JSON payload. The response is never cached because build availability can change often while individual build bytes remain immutable.

```json
{
  "title": "The Metamorphosis",
  "author": "Franz Kafka",
  "source": "gutenberg",
  "renditions": {
    "kokoro-af-heart": {
      "voice": "af_heart",
      "engine": "kokoro",
      "display": "Heart",
      "current_build": "2a4f9c1b3d8e7f60",
      "builds": [
        {
          "build": "2a4f9c1b3d8e7f60",
          "rendition": "kokoro-af-heart",
          "voice": "af_heart",
          "engine": "kokoro",
          "pipeline_version": "1",
          "is_current": true,
          "uploaded_at": "2026-06-14T18:42:00.000Z",
          "total_duration_seconds": 1847.3,
          "chapter_count": 1,
          "chapters": [
            {"number": 1, "title": "I", "filename": "chapter-01.m4a",
             "duration_seconds": 1847.3, "word_count": 3241}
          ]
        }
      ]
    }
  }
}
```

Omitting `build` in client URLs keeps the default behavior: the reader resolves the selected rendition to `current_build`. Passing `build=<retained build id>` pins the reader to that build for chapter data and audio.

## Cache policy

The cache header on a route is determined by **whether the URL is content-versioned**, not by the resource type:

| URL form | Cache-Control | Rationale |
|---|---|---|
| `/books/:a/:t` (book manifest) | `public, max-age=60, stale-while-revalidate=86400` | Mutable pointer; the only place a new build's existence can be discovered |
| `/books/:a/:t/builds` (build selection) | `no-store` | Mutable discovery view over retained builds; build availability can change frequently |
| `/books/:a/:t/cover`, `/epub` | `public, max-age=31536000, immutable` | Bytes never change for a book |
| `/books/:a/:t/chapters/:n?rendition=&build=` | `public, max-age=31536000, immutable` | Build pin in URL ⇒ bytes never change |
| `/books/:a/:t/audio/:c?rendition=&build=` | `public, max-age=31536000, immutable` | Same as above |
| `/catalog` | `public, max-age=60, stale-while-revalidate=86400` | Mutable index of books |

**Invariant:** if the response is sensitive to a build hash, that hash MUST be in the URL (query or path). Never serve different bytes from the same URL with `immutable`. The book manifest is the sole exception — it is the discovery layer that points at immutable build URLs and therefore must be short-cached.

## `GET /catalog` source of truth

The preferred source is root `catalog.json`, written by `openshelf-pipeline ops catalog` and refreshed by `openshelf-pipeline books process --upload`. Catalog rows include the selected default rendition and its `current_build`. If that mutable index is missing, the worker derives the same response shape by listing `books/`, reading root `books/:author/:title/manifest.json` objects, selecting the default rendition (`kokoro-af-heart`, or the first sorted rendition), and enriching each row from that rendition's `current_build` `rendition-manifest.json`.

The fallback exists so a refactor or partial migration that uploads book manifests before rebuilding `catalog.json` does not make the public catalog appear empty. It is not a replacement for the pipeline catalog build; the generated `catalog.json` remains the normal fast path.

## Rendition vs build

- **Rendition** (`kokoro-af-heart`) is user-facing — chosen by the user, exposed in the catalog and book manifest.
- **Build** (`2a4f9c1b3d8e7f60`) is internal — a 16-hex string the pipeline assigns once per run (no content addressing). The default client treats it as transparent, while the build-selection route can expose retained IDs for rollback/testing.

The client treats rendition as a setting and build as transparent by default: it reads the book manifest, looks up `current_build` for the user's chosen rendition, pins that hash for the duration of the chapter session, and includes it in chapter and audio URLs as `?build=...`. If an explicit retained build is selected, the client passes that build instead, stores the choice locally as the book's preferred default, and scopes reading progress by book/rendition/build. New backend current builds are picked up when no explicit local build preference is retained.

## Adding or modifying a route

1. Read `docs/openapi.md` for the authoring pattern.
2. Update the route's Zod schemas (params/query/response/error) — this is the spec change.
3. Implement against the schema (the type system will refuse anything else).
4. Run `npm run typecheck && npm test`. Tests must stay green; if a 200 schema mismatches the response, fix the handler or the schema — do **not** loosen the schema to make tests pass.
5. Hit `/api/v1/openapi.json` and `/api/v1/docs` in dev to confirm the spec renders.

## Environments

- **staging**: `openshelf-api-staging` worker, `openshelf-staging` R2 bucket
- **production**: `openshelf-api` worker, `openshelf` R2 bucket,
  `openshelf-jobs` D1, separate owner/PC secrets, and search/failed-auth limits.
  Apply D1 migrations and seed the source index before exposing generation.
  The top-level Wrangler bindings also name production resources because
  Cloudflare Workers Builds runs `wrangler versions upload` without `--env` for
  PR previews; local `wrangler dev` still uses local D1 storage by default.

Production deploys are automated by Cloudflare Workers Builds / Git integration:

- Repo: `jkoeh/openshelf`
- Root directory: `worker`
- Production branch: `main`
- Install/build command: `npm ci`
- Deploy command: `npm run deploy:production`

The deploy command uses `[env.production]` in `wrangler.toml`, so it publishes
the `openshelf-api` Worker with the production R2 bucket binding.
