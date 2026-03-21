# Worker — CLAUDE.md

## What This Is

Cloudflare Worker API that serves audiobook data from R2. Built with Hono and TypeScript.

## Structure

```
src/
  index.ts              # Hono app, route mounting, error handling
  types.ts              # Env bindings, shared types
  constants.ts          # Default rendition, cache headers
  middleware/
    cors.ts             # CORS middleware
  routes/
    health.ts           # GET /api/v1/health
    catalog.ts          # GET /api/v1/catalog
    book.ts             # GET /api/v1/books/:author/:title
    chapters.ts         # GET /api/v1/books/:author/:title/chapters/:number
    audio.ts            # GET /api/v1/books/:author/:title/audio/:chapter
    epub.ts             # GET /api/v1/books/:author/:title/epub
    alignment.ts        # GET /api/v1/books/:author/:title/alignment
  utils/
    r2-keys.ts          # R2 key builders
    response.ts         # Error response helpers
    validation.ts       # Param validation

tests/
  routes/               # Vitest + @cloudflare/vitest-pool-workers tests
```

## Stack

- TypeScript, Hono v4
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
- All R2 key construction goes through `utils/r2-keys.ts`
- Error responses use `utils/response.ts` helpers
- Tests use `@cloudflare/vitest-pool-workers` with fixture data in `fixtures/`

## Environments

- **staging**: `openshelf-api-staging` worker, `openshelf-staging` R2 bucket
- **production**: `openshelf-api` worker, `openshelf` R2 bucket
