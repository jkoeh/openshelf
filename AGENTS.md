# AGENTS.md

## What This Project Is

**OpenShelf** is an open source public domain audiobook platform. It imports validated English and Chinese editions from approved Gutenberg mirrors/bulk access, Standard Ebooks and Wikisource API snapshots, caches reading EPUBs independently of audio, converts them to AI-narrated audio through swappable TTS engines, aligns final audio with WhisperX word timestamps, and serves it globally via Cloudflare R2. `pipeline/docs/source-access.md` specifies identity, provenance, rights, caching and language support.

## End-to-End Flow

The pipeline produces audio + word-aligned text; R2 stores it; the worker serves it; the client streams audio while highlighting the current word.

```mermaid
flowchart LR
    subgraph Pipeline[Pipeline — Python]
        direction TB
        P1[EPUB] --> P2[parse_epub<br/>typed Sections + heading/body ContentElements]
        P2 --> P3[text_chunker<br/>body chunks only]
        P3 --> P3B[voice_director<br/>registry + speaker spans + engine-aware direction]
        P3B --> P4[TTSEngine adapter<br/>Kokoro, Qwen3, F5-TTS, or Chatterbox]
        P4 -->|heading + body WAV<br/>+ region word timestamps<br/>+ synthesis seam audit| P5[encode_to_aac<br/>m4a]
        P3B --> P6A[character_registry.json<br/>voices + aliases]
        P3B --> P6B[voice_direction.json<br/>speaker + performance audit]
        P4 --> P6[section_data.json<br/>heading + body chunks + words]
        P5 --> P7[rendition-manifest.json<br/>per-build section list]
        P2 --> P8[annotated EPUB<br/>+ cover]
        P9[new_build_id<br/>fresh random 16-hex per run] --> P5
        P9 --> P0[run.json<br/>per-build resume context]
        P9 --> P6
        P9 --> P7
        P7 --> P10[book manifest.json<br/>renditions[].current_build]
    end

    subgraph R2[Cloudflare R2]
        direction TB
        R_EPUB[book.epub]
        R_COVER[cover jpg/png]
        R_BOOKMAN[manifest.json<br/>book-level pointer · MUTABLE]
        R_REG[audio/{rendition}/builds/{build}/<br/>character_registry.json]
        R_DIR[audio/{rendition}/builds/{build}/<br/>voice_direction.json]
        R_SYN[audio/{rendition}/builds/{build}/<br/>section-NN.synthesis_units.json]
        R_RUN[audio/{rendition}/builds/{build}/<br/>run.json]
        R_RMAN[audio/{rendition}/builds/{build}/<br/>rendition-manifest.json]
        R_M4A[audio/{rendition}/builds/{build}/<br/>section-NN.m4a]
        R_CD[audio/{rendition}/builds/{build}/<br/>section_data.json]
        R_CAT[catalog.json<br/>publication audit + backfill artifact]
        R_SOURCE[source-epubs/{provider}/{source-hash}/book.epub<br/>validated reading cache + provenance/hash]
        R_UPLOAD[owner-uploads/{uuid}/original.{format}<br/>authenticated owner originals]
    end

    R_JOBS[(D1 source index + publication catalog + generation jobs)]

    P5 --> R_M4A
    P6 --> R_CD
    P6A --> R_REG
    P6B --> R_DIR
    P4 --> R_SYN
    P0 --> R_RUN
    P7 --> R_RMAN
    P10 --> R_BOOKMAN
    P8 --> R_EPUB
    P8 --> R_COVER

    subgraph Worker[Worker — Cloudflare/Hono + zod-openapi]
        direction TB
        W_CAT[GET /catalog<br/>current D1 publication rows · no-store<br/>book + default rendition/build]
        W_CAT_SYNC[PC-authenticated catalog sync<br/>derive publication rows from R2 manifests]
        W_BOOK[GET /books/:a/:t<br/>→ book manifest with current_build per rendition]
        W_MANAGE[Owner book management<br/>regenerate source + delete publication]
        W_BUILDS[GET /books/:a/:t/builds<br/>-> available renditions/build uploads]
        W_CH[GET /books/:a/:t/sections/:n<br/>?rendition · ?build<br/>→ heading + body + region words]
        W_AUDIO[GET /books/:a/:t/sections/:n/audio<br/>?rendition · ?build<br/>→ m4a stream / range]
        W_COVER[GET /books/:a/:t/cover]
        W_EPUB[GET /books/:a/:t/epub]
        W_SPEC[GET /openapi.json + /docs<br/>auto-generated from Zod schemas]
        W_SEARCH[GET /source-books<br/>bounded search + cached Chinese browse]
        W_SOURCE_EPUB[GET /source-books/:source_id/epub<br/>R2 reading copy independent of upstream/audio]
        W_ADMIN[GET /admin/me<br/>verify Google owner]
        W_UPLOAD[Owner document upload/list<br/>PC authenticated original download]
        W_JOBS[Public standard audio requests/status<br/>Owner cancel/retry/regenerate/priority<br/>Owner bounded queue and recent history<br/>Owner OpenAI-directed Chatterbox requests<br/>PC claim/heartbeat/finish]
    end

    R_JOBS --> W_CAT
    R_BOOKMAN --> W_CAT_SYNC
    R_RMAN --> W_CAT_SYNC
    W_CAT_SYNC --> R_JOBS
    R_BOOKMAN --> W_BOOK
    R_BOOKMAN --> W_BUILDS
    R_RMAN --> W_BUILDS
    R_CD  --> W_CH
    R_M4A --> W_AUDIO
    R_COVER --> W_COVER
    R_EPUB --> W_EPUB
    R_JOBS --> W_SEARCH
    R_JOBS --> W_SOURCE_EPUB
    R_SOURCE --> W_SOURCE_EPUB
    R_UPLOAD --> W_UPLOAD
    R_JOBS --> W_JOBS
    W_JOBS --> R_JOBS

    subgraph Client[Client — Expo]
        direction TB
        C1[Catalog page<br/>fetchCatalog]
        C_SEARCH[Source search + generation status<br/>Read now: EPUB reader + audio job]
        C_SOURCE[Source reader<br/>OPF spine text + animated owl stage progress<br/>Start Listening when published]
        C_ADMIN[Google owner sign-in<br/>cancel/retry/regenerate<br/>request expressive audio]
        C2[Book detail<br/>fetchBook → manifest with renditions]
        C2B[Collapsed rendition picker<br/>engine -> voice -> upload time]
        C3[Reader page<br/>pin build at section load<br/>fetchSection rendition build → heading + body + words]
        C3 --> C4[expo-audio player<br/>streams m4a rendition build]
        C3 --> C5[useSyncEngine<br/>rAF reads player.currentTime]
        C5 -->|findWordAtTime| C6[active word / chunk]
        C6 --> C7[ReadingPane highlights word]
        C6 -.tap word.-> C4
    end

    W_CAT  --> C1
    W_SEARCH --> C_SEARCH
    C_SEARCH --> W_JOBS
    C_SEARCH --> W_SOURCE_EPUB
    C_SEARCH --> C_SOURCE
    W_SOURCE_EPUB --> C_SOURCE
    SE_SITE[Standard Ebooks public website] -->|legacy exact EPUB fallback on cache miss| C_SOURCE
    W_JOBS --> C_SOURCE
    C_SOURCE --> C3
    C_ADMIN --> W_ADMIN
    C_ADMIN --> W_UPLOAD
    C_ADMIN --> W_JOBS
    C_ADMIN --> W_MANAGE
    W_BOOK --> C2
    W_BUILDS --> C2B
    C2B --> C3
    W_CH   --> C3
    W_AUDIO --> C4

    subgraph PC[Owner PC — outbound consumer]
        J_MON[Windows Studio · embedded local dashboard<br/>owns server + consumer process tree<br/>priority + cancel + retry + consumer status]
        J_IMPORT[Bulk Gutenberg / Standard Ebooks / Wikisource API<br/>verify identity, rights, language, order + provenance] --> R_SOURCE
        J_IMPORT --> R_JOBS
        J1[Poll + lease job] --> J2[Cached exact EPUB or approved source access]
        J2 --> J2R[Verify source-specific public-domain rights + EPUB identity]
        J_DOC[Owner PDF/DOCX/TXT extraction<br/>rights acknowledgement + ordered EPUB] --> J3
        J2R --> J3[Standard: English Kokoro / Mandarin Qwen3 Serena<br/>Expressive: English OpenAI + Chatterbox<br/>language-aware WhisperX + R2]
    end
    J1 <--> W_JOBS
    J_MON <--> W_JOBS
    J3 --> R_M4A
    J3 --> R_CD
    W_UPLOAD --> J_DOC
```



Notes:

- The version-2 `section_data.json` produced in step P6 is the single source for
  heading display/spoken text, body chunk text, and word timestamps. Heading
  and body are separate alignment regions; there is no separate alignment
  fetch on the happy path.
- Voice direction writes auditable per-build artifacts to R2. `character_registry.json` carries the narrator, character aliases, descriptions, and assigned voices for future client features. `voice_direction.json` carries the cast mode plus speaker/performance plan used for synthesis. TTS also writes `section-NN.synthesis_units.json` as a per-section seam audit with internal unit boundaries and inserted pause metadata for repair tooling. The reader consumes `section_data.json` for heading display/spoken text, body text, and word sync.
- The default cast mode is **solo narrator**: the LLM may choose the narrator and build/expand the character registry, but synthesis keeps one narrator voice for the whole audiobook. Character data is used for audit and future style guidance rather than hard voice switching. **Multicast** is opt-in via config/CLI and may switch voices by character; it is experimental because prose dialogue tags can sound stitched when rendered as separate synthetic actors. WhisperX is the canonical word-timestamp source for every engine; Kokoro native token timestamps are not used for the final sync contract.
- `GET /catalog` queries the D1 publication index and returns `no-store` for browsing and search. PC-authenticated publication sync derives metadata from actual R2 root/rendition manifests; normal job completion and publication deletion update this index. `catalog.json` remains a publication audit/backfill artifact. Existing manual publications are backfilled before client rollout. Source and published searches normalize simplified/traditional Chinese while returning original metadata. Catalog rows include the default rendition and current build so the client can open a coherent snapshot.
- `GET /books/:a/:t/builds` is the no-cache discovery API for build selection. It reads the book manifest's retained `available_builds`, enriches each retained build from its own `rendition-manifest.json`, and returns `uploaded_at` from the R2 object's upload timestamp. Omitting `build` in client URLs still means use the rendition's `current_build`.
- The client stores the user's preferred build per book locally and uses that preference on the next book-detail load when the retained build still exists. Reading progress is keyed by book, rendition, and build so progress from one build cannot leak into another. The book-detail selector is collapsed by default, always shows the selected engine, groups choices by engine then voice, and lists builds by upload time while keeping raw build IDs internal.
- The client does not poll status for sync; `useSyncEngine` reads `player.currentTime` directly inside `requestAnimationFrame` and only re-renders when the active word index changes.
- Tap-to-seek in the reader looks up `words[i].start` and calls
  `player.seekTo`. Tapping the heading seeks to its first heading word.
- Verified web owners can regenerate or delete from the published book detail
  screen. Owner identity is shared across catalog and book routes in React memory.
  Regeneration queues a fresh standard or expressive build for the indexed source
  while the existing audio remains playable. Deletion requires an explicit in-app
  confirmation and removes all published renditions/builds, EPUB, cover and catalog
  entry for that book. Active generation must be canceled or completed first.
  Recently canceled running work gets a 120-second grace period before deletion
  so the PC can stop its child at the next heartbeat or lease-renewal timeout.
  A D1 deletion guard excludes concurrent generation and overlapping deletion;
  interrupted deletion can be retried. Source reading/index metadata is retained,
  publication pointers are cleared, and prior jobs are retired from retry.
  Cached public responses can remain visible until their existing cache expires.
- When section audio finishes, the reader advances to the next section in the
  same selected rendition/build and starts playback only after the next
  section's audio source has loaded.
- The owner PC consumer checks source EPUB ZIP metadata, expanded size,
  entry count, and parsed source spoken words before starting LLM or synthesis.
  Standard and expressive jobs have no word-count limit or `--max-words` option.
  Parsed spoken text must still be nonempty, including spoken headings.
- Source search offers one **Read now** action. It opens a source EPUB reader immediately, fetches the validated R2 EPUB with `inline=1`, and independently creates or follows an audio job. Failed/canceled audio and queue/start limits never block text. The reader follows the OPF spine and renders plain text. An animated owl shows stage milestones (0% queued, 25% preparation, 50% synthesis/alignment/encoding/upload, 100% complete), honors reduced motion, and becomes Start Listening when published. Cached copies are served for both inline and direct downloads; inline misses fail explicitly without upstream access. Legacy direct Gutenberg/Standard Ebooks downloads may redirect through approved source access. The PC bounds and validates upstream redirects against the exact source ID. The annotated R2 EPUB endpoint remains available; the UI uses Read now instead of download buttons.
- Generation requests for fixed local English Kokoro or Mandarin Qwen3 Serena voices may be public, subject to
  the atomic daily-start and queue caps. An expressive request is owner-only and
  selects an English-only fixed Chatterbox `af_heart` rendition with batched OpenAI emotion
  direction. The OpenAI key remains on the PC; the browser cannot select an
  arbitrary model, engine, voice, or prompt. Browser admin actions accept a Google
  Identity Services ID token only after the Worker verifies its signature,
  issuer, audience, expiry, verified email, and exact `johnkoeh@gmail.com`
  address. The existing local owner token remains valid for CLI administration.
  Browser tokens remain in memory and are never logged or persisted. The
  web Owner controls modal offers Google sign-in, loading/retry/error feedback,
  and sign-out; it closes after verified sign-in and enables owner job actions
  in search results. Its backdrop and close button keep mobile discovery readable.
  Both the deployed client build and Worker need the same public OAuth client ID.
  Only the owner can cancel, retry, regenerate, or request the expressive mode. The
  300-start daily cap and three-job queue cap cover both modes.
  Cancellation is a terminal job state: queued
  work cannot be claimed, and a running consumer loses its lease on the next
  heartbeat and stops its child process. A canceled start is not refunded.
  Owner retry accepts failed or canceled jobs with fewer than three attempts,
  preserves the original job/build, clears its error, and atomically reserves
  a new daily start when returning the job to the capped queue.
- The local Windows monitor reads a bounded owner-only job list every 15 seconds.
  It shows queued, running, expired-lease (stuck), and recent terminal jobs,
  plus the local consumer process and log. Queued jobs have normal or high
  priority; claim takes high-priority queued jobs first and preserves FIFO within
  each level. Priority changes do not preempt a running job or bypass start and
  queue caps. Cancel uses the existing owner endpoint and revokes a running
  lease; the PC stops its child at the next heartbeat. The monitor loads owner
  and PC credentials only from ignored local files. Its browser dashboard binds
  only to 127.0.0.1 on a random port; every data/control request requires a random
  session token, exact Host validation, and same-origin checks. No CORS is enabled.
  Credentials never reach the browser. Failed/canceled jobs can be retried;
  the consumer can be started or stopped while idle. The Windows Studio launcher
  embeds the dashboard in a private WebView2 window, automatically starts or
  adopts the exact production consumer, and uses a Windows Job Object to own its
  full process tree. Closing the window stops the HTTP server and all owned
  consumer/synthesis processes, including on application crash. Interrupted jobs
  remain leased until expiry, then can be reclaimed within the existing
  three-attempt cap on next launch with the same
  build's existing resume behavior. Repeated launches focus the existing window.
  Authentication is supplied only to local API requests by the native host;
  users never need to save a URL or token. Static assets live beside
  job_monitor_web.py in pipeline/src/openshelf/pipeline/monitor/; desktop lifecycle
  and Windows ownership are implemented in job_monitor_desktop.py.

Chinese discovery offers a visible cached-edition browse filter with current
counts and pagination, independent of published audio. Source reader display
preserves Chinese paragraph starts and explicit breaks without rewriting EPUBs
or alignment text. Mandarin Start Listening selects Qwen Serena explicitly.

### Rendition vs build invariant

OpenShelf separates two orthogonal concepts that used to be conflated:

- **Rendition** is a user-facing artistic identity (a narrator voice + engine). Examples: `kokoro-af-heart`, `kokoro-bf-emma`, `f5tts-custom`, `chatterbox-custom`. Stable across pipeline changes. The user picks a rendition.
- **Build** is an internal pipeline-output identity. It is a fresh random 16-hex string by default, or a caller-provided 16-hex string when resuming a targeted build. The default client experience hides it; retained build choices are presented by upload time while the build ID remains the URL/storage key.

Storage and HTTP URLs include **both**: `audio/{rendition}/builds/{build}/...` on R2; `?rendition=...&build=...` on every immutable HTTP route. The book-level `manifest.json` is the single mutable pointer that names the `current_build` per rendition. This makes audio + section_data + rendition-manifest a coherent atomic snapshot per (rendition, build), so a client can never mix bytes from different builds mid-session.

Each build prefix may include `run.json`, the local/resumable context artifact
that records the EPUB hash and immutable invocation settings for that build.
When `--build-id --resume` targets an existing local build, `run.json` must match
the current invocation before expensive downstream work continues.

This is the contract that lets every per-build URL set `Cache-Control: immutable` honestly. Only the book manifest carries a short cache.

## Monorepo Structure

```
pipeline/               # Python — EPUB ingestion, TTS, R2 upload
  src/openshelf/        # Python package
  scripts/              # CLI entry points + local browser monitor launcher
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
  astra-prototype/      # standalone Astra game experiment; SPEC.md, static dist/, local model preview
design-qa.md             # catalog visual comparison and responsive QA record
.github/workflows/      # full offline Python/API/client and browser regression gates
scripts/                # PC setup and local monitor launcher
```

## Root Commands

```bash
npm run dev          # Start worker + client concurrently
npm run dev:window   # Windows-safe dev start; local worker + client, no Wrangler login required
npm run dev:window:r2 # Windows-safe dev start; local worker + client with remote R2
npm run test         # Run worker + client tests
npm run typecheck    # Type-check worker + client
```

### Regression gates

CI installs CPU-only test dependencies from `pipeline/requirements-test.txt` and
runs the full offline Python suite with `python -m unittest discover -s pipeline/tests -t . -v`.
Model downloads, GPU synthesis and live alignment remain opt-in integration QA.
The client has mocked browser flows (`npm run test:e2e`) and a separate real API
browser suite (`npm run test:e2e:integration`, both from `client/`). The latter
uses a temporary local Worker/D1/R2 harness under `worker/tests/browser/`, applies
all migrations, and never loads production credentials or remote bindings.
It checks cached Chinese/English reading with upstream unavailable, audio job
routing/publication, original text/order, real media playback, word seek/highlight,
section advance and isolation from later builds. A fixture producer supplies
EPUB/AAC/alignment artifacts in place of the PC; this gate does not certify
model quality or replace the pipeline's validation tests.
Playback controls expose accessible Play/Pause and previous/next section labels
so browser checks operate the same controls as readers.

## Conventions

- Pipeline code requires Python 3.12 or newer. CI uses Python 3.12; the isolated
  Mandarin runtime also uses 3.12.
- Owner-only document ingestion accepts text PDF, DOCX, UTF-8 TXT and pasted
  text, with explicit rights/publication acknowledgement. Originals are private
  authenticated R2 objects; completed audio/annotated EPUBs are public. Hidden
  owner source rows never enter source discovery. See `pipeline/docs/source-access.md`
  for supported inputs, extraction bounds and operator-controlled rights.

- Each component owns its dependency file (`pipeline/requirements.txt`, `worker/package.json`, `client/package.json`)
- Shared data contract: pipeline writes JSON (manifest, section_data,
  character_registry, voice_direction) to R2. `section_data.json` carries
  heading metadata, body chunks, and region-tagged word timestamps in a single
  file, regardless of TTS engine; direction artifacts are audit/client-feature
  metadata and must not replace reader text.
- EPUB spine order determines `Section.sequence`, never chapter ordinal.
  Structural types and ordinals come from EPUB semantics, headings, navigation,
  and conservative filename fallbacks. Opening/closing credits are generated
  sections; source title pages are not playback sections.
- `sanitize()` in `pipeline/src/openshelf/scrapers/http.py` is the single source of truth for slug generation
- Idempotent at every level: file exists -> skip, R2 key exists -> skip
- Automatic source catalog import and PC job generation require verified
  U.S. public-domain status. Public reading remains available globally. Manual
  `books process` and `dag run` uploads are operator-controlled; the operator
  is responsible for checking rights before publishing a local EPUB.

### Standard Ebooks source discovery

Source search includes indexed Gutenberg, Standard Ebooks and Wikisource editions. Standard
Ebooks IDs are `standardebooks:<author>/<title>[/<contributors>...]`, preserving
the canonical edition path. The PC imports at most 1,000 editions per run through
`books sync-catalog --source standard-ebooks`, with a two-second request delay,
bounded listing pages, and batches of at most 50. Each edition must have the
official U.S. public-domain statement and an exact compatible EPUB link; future
`not-pd` listings and ambiguous metadata are skipped. Every source search queries
our own D1 index with no response cache or upstream search fallback. Simplified
and traditional Chinese queries match the same indexed editions while returned
metadata and reading text retain the source script. Imports populate the index;
unimported books are not invented as search results. Our abuse limiter remains,
but upstream search availability and upstream rate limits cannot affect queries.
The client labels the source of each edition.

Reading and generation allow only HTTPS EPUB URLs matching the exact edition on
`standardebooks.org`; redirects are bounded and revalidated. No download-cookie
gate is bypassed. The official exact-file `?source=download` continuation is
allowed; other query strings are rejected. An upstream HTML response fails explicitly. Before caching,
the PC checks the edition page, verifies the EPUB OPF identifier matches that
edition and its rights dedicate the edition to the public domain via CC0.
Standard Ebooks rejects direct Cloudflare fetches with HTTP 403. During a real
catalog import the PC downloads and verifies each exact EPUB before indexing it,
then sends it to the PC-authenticated internal source-EPUB upload route. The
Worker stores it at `source-epubs/standardebooks/{sha256(source_id)}/book.epub`.
Uploads are bounded to 32 MiB, require EPUB ZIP magic, and are accepted only for
an indexed source ID (all three providers use the same authenticated cache route).
Reading streams this cache with `no-store` independently of upstream availability;
missing caches fail explicitly. On a cache miss (404/502), the reader fetches
the exact official Standard Ebooks EPUB directly, using its canonical source ID
and the same 32 MiB bound. Direct requests omit credentials and reject redirects;
HTTP 403/429 is shown without retrying automatically. Legacy exact Standard
Ebooks links remain readable through this fallback; search does not query the
public website to discover them.
Direct downloads serve cached copies first and redirect only on a legacy cache miss.
`--dry-run` validates metadata without downloading or uploading EPUBs. Source
cache bytes are independent of audiobook publication and are retained on refresh.
Imports check the cache with a HEAD request before downloading an EPUB again.
HTTP 403 or 429 stops the import immediately with a nonzero status, preserving
completed entries; operators may resume later without repeating cached downloads.
Standard Ebooks publication slugs append `-se-` plus the first 12 hex characters
of SHA-256 of the source ID; Gutenberg retains `-g<id>`. Both generation modes
share existing limits, leases, resume behavior, and publication checks.

### Gutenberg, Wikisource and Chinese source access

`pipeline/docs/source-access.md` is the source-access spec. Gutenberg bulk imports
support English and Chinese and use the listed HTTPS mirror or a local rsync
collection. Wikisource imports use curated API snapshots pinned to root/chapter
revisions, explicit edition order and verified U.S. rights evidence. Cached
editions retain provenance and content hashes. Consumer jobs validate cached
hashes, embedded rights/identity and language before synthesis; legacy uncached
Gutenberg/Standard Ebooks jobs also recheck upstream rights. Chinese standard
jobs use Qwen3-TTS 1.7B CustomVoice Serena (`qwen3-serena`) and WhisperX `zh`.
The PC uses an isolated Mandarin Python runtime, advertises Mandarin claim
capability only when configured, and pins checkpoint/settings in `run.json`.
Existing Kokoro Mandarin renditions retain their identity. Chinese source script and punctuation
are retained in reading and sync. Expressive generation remains English-only.

## Docs-First Workflow (mandatory)

Documentation in this repo is treated as the **spec**, not a trailing description of code. Every change follows this order:

1. **Identify the spec.** Locate every doc that describes the behavior you're about to change. The relevant files are:
  - `AGENTS.md` (this file — end-to-end flow, data contracts, cross-cutting rules)
  - The component's `AGENTS.md` (`pipeline/`, `worker/`, `client/`)
  - The relevant pipeline step doc under `pipeline/docs/step*.md`
  - The end-to-end mermaid flow above (if data shape, R2 layout, or routes change)
  - Inline interface blocks (dataclasses, route shapes, R2 key layout) inside those docs
2. **Update the docs first.** Edit the docs and mermaid flow to describe the **target** behavior. Pipeline data shape, R2 keys, worker route response shape, and client hook signatures must match what the code will be after the change.
3. **Implement against the updated docs.** Write code, types, and tests so they conform to what the doc now says. The doc is the contract; the code mirrors it.
4. **Verify alignment before committing.** Re-read the relevant doc sections and confirm the diff matches. Run typecheck + tests.

### Worker contract is machine-generated

The worker's request/response shapes are **not** described in prose. Each route is a Zod schema in `worker/src/routes/*.ts`; `@hono/zod-openapi` synthesizes `/api/v1/openapi.json` from those schemas at request time. To change a route's shape, edit the Zod schema — the type system, runtime validation, and the OpenAPI doc all update together. Treat `/api/v1/openapi.json` (and `/api/v1/docs`) as the worker's spec; this AGENTS.md and `worker/AGENTS.md` only describe **why** routes exist, not their wire shape. See `worker/docs/openapi.md` for the authoring pattern.

### Out-of-sync detection — STOP and ask

Before editing anything in a region (a module, a route, a hook, an R2 key layout), spot-check that the existing doc agrees with the existing code. If you find a mismatch — e.g., the doc claims a function returns X but the code returns Y, or a route is documented but missing, or an R2 key listed in `step6-r2.md` is no longer written by the pipeline — **do not silently "fix" the doc and proceed**. Flag it to the user and ask which side is correct. Drift usually means in-flight work, an aborted refactor, or a bug, and resolving it incorrectly can erase intent.

A change is only complete when docs, code, and tests all describe the same system.

## Do NOT

- Over-engineer — no abstractions until there are 2+ concrete uses
- Create files outside the established structure without updating this doc or the component's AGENTS.md
- Edit code before updating the spec docs above. If the docs are wrong or missing, fix the docs first.
- Silently reconcile a docs/code mismatch — surface it and ask.
