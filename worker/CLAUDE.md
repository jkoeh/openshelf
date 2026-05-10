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
  constants.ts          # Default rendition, cache headers
  middleware/
    cors.ts             # CORS middleware
  schemas/
    error.ts            # ErrorSchema — { error: { code, message } }
    params.ts           # SlugSchema, ChapterNumberStringSchema (shared path params)
  routes/               # one OpenAPIHono subapp per file; each defines route(s) via createRoute
    health.ts           # GET /api/v1/health
    catalog.ts          # GET /api/v1/catalog
    book.ts             # GET /api/v1/books/:author/:title
    chapters.ts         # GET /api/v1/books/:author/:title/chapters/:number?rendition=&build= — text + inline word timestamps (immutable)
    audio.ts            # GET /api/v1/books/:author/:title/audio/:chapter?rendition=&build= — m4a stream, supports Range (immutable)
    cover.ts            # GET /api/v1/books/:author/:title/cover
    epub.ts             # GET /api/v1/books/:author/:title/epub
    alignment.ts        # GET /api/v1/books/:author/:title/alignment[/:chapter] — legacy, --whisperx only
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
- R2 bucket binding (`R2_BUCKET`)
- Vitest + miniflare for testing
- Biome for linting/formatting

## Commands

All commands run from the **worker/** directory.

```bash
# Dev server
npm run dev

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

## Conventions

- Biome enforced: tabs, 100 char line width, LF line endings
- Routes are thin — extract params, fetch from R2, return JSON
- Each route is defined by a Zod-driven `createRoute(...)` block + a handler. The Zod schema is the contract: validation, types, and the OpenAPI spec all flow from it. **Never** hand-edit `openapi.json`; change the schema instead.
- All R2 key construction goes through `utils/r2-keys.ts`
- Inside `app.openapi(...)` handlers, return errors with inline `c.json({ error: { code, message } }, status)` so the response is type-checked against `ErrorSchema`. The helpers in `utils/response.ts` are reserved for the global `onError`/`notFound` (which run outside any `createRoute`).
- Path/query schemas live in `schemas/params.ts` if shared across routes; route-local response shapes live in the route file.
- Tests use `@cloudflare/vitest-pool-workers` with fixture data in `fixtures/`. They use `app.request(...)` and are unaffected by the OpenAPI migration.
- `chapters.ts` reads `audio/{rendition}/builds/{build}/chapter_data.json` (single source of truth for chunk text + word timestamps). It flattens per-chunk word arrays and adds `chunk_idx` per word in the response.

## Cache policy

The cache header on a route is determined by **whether the URL is content-versioned**, not by the resource type:

| URL form | Cache-Control | Rationale |
|---|---|---|
| `/books/:a/:t` (book manifest) | `public, max-age=60, stale-while-revalidate=86400` | Mutable pointer; the only place a new build's existence can be discovered |
| `/books/:a/:t/cover`, `/epub` | `public, max-age=31536000, immutable` | Bytes never change for a book |
| `/books/:a/:t/chapters/:n?rendition=&build=` | `public, max-age=31536000, immutable` | Build pin in URL ⇒ bytes never change |
| `/books/:a/:t/audio/:c?rendition=&build=` | `public, max-age=31536000, immutable` | Same as above |
| `/catalog` | `public, max-age=60, stale-while-revalidate=86400` | Mutable index of books |

**Invariant:** if the response is sensitive to a build hash, that hash MUST be in the URL (query or path). Never serve different bytes from the same URL with `immutable`. The book manifest is the sole exception — it is the discovery layer that points at immutable build URLs and therefore must be short-cached.

## Rendition vs build

- **Rendition** (`kokoro-af-heart`) is user-facing — chosen by the user, exposed in the catalog and book manifest.
- **Build** (`2a4f9c1`) is internal — a 7-char content hash from the pipeline. Surfaced only via the book manifest's per-rendition `current_build`.

The client treats rendition as a setting and build as transparent: it reads the book manifest, looks up `current_build` for the user's chosen rendition, pins that hash for the duration of the chapter session, and includes it in chapter and audio URLs as `?build=...`. New builds are picked up on the next chapter or app restart.

## Adding or modifying a route

1. Read `docs/openapi.md` for the authoring pattern.
2. Update the route's Zod schemas (params/query/response/error) — this is the spec change.
3. Implement against the schema (the type system will refuse anything else).
4. Run `npm run typecheck && npm test`. Tests must stay green; if a 200 schema mismatches the response, fix the handler or the schema — do **not** loosen the schema to make tests pass.
5. Hit `/api/v1/openapi.json` and `/api/v1/docs` in dev to confirm the spec renders.

## Environments

- **staging**: `openshelf-api-staging` worker, `openshelf-staging` R2 bucket
- **production**: `openshelf-api` worker, `openshelf` R2 bucket
