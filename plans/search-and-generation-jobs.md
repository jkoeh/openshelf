# Plan: book discovery and PC audio generation

**Status:** first Gutenberg/Kokoro vertical slice implemented, tested, and
validated in isolated staging with a real *The Tale of Peter Rabbit* GPU job.
Production D1, Worker bindings, credentials, and initial source index are
provisioned. Remaining work packages cover broader source and engine support.
This document also records the original design and cost assumptions. The
section-based version-2 playback contract remains the output of every job.

## Outcome

From the existing Expo client, an owner can type a title or author, choose a
specific downloadable Gutenberg or Standard Ebooks edition, request an
audiobook, watch its status, and open the resulting rendition when ready. A
process running on the owner's Windows PC takes jobs when online, performs the
existing Python pipeline, and publishes to R2. A stopped PC leaves jobs queued.

The first vertical slice uses Gutenberg, Kokoro with an explicit `af_heart`
voice, one concurrent job, and the existing root `.venv` with CUDA. Standard
Ebooks, other engines, and additional workers follow through the same contracts.
Normal catalog browsing and playback continue to use the existing R2 routes.

```mermaid
flowchart LR
    Client[Expo client] -->|Search, Generate, status, playback| Limit[Worker request limits]
    Limit --> API[Worker API]
    API -->|Indexed suggestions| Sources[(D1 source books)]
    API -->|Owner auth, quota, dedupe| Jobs[(D1 jobs)]
    PC[PC consumer] -->|Outbound claim with PC credential| Limit
    API -.->|Claim response: leased job| PC
    PC --> Pipeline[Local EPUB, TTS, WhisperX pipeline]
    Pipeline --> R2[(R2 audio and text)]
    PC -->|Progress and completion| Limit
    API -->|Verify published build| R2
    API -->|Responses| Client
```

## Decisions and boundaries

- The PC **initiates outbound HTTPS polling**. No publicly reachable listener,
  port forwarding, or permanently running Cloudflare Worker is required. The
  process is a `books consume-jobs` CLI/service, even if the operator calls it a
  server.
- D1 holds the searchable source-book index and durable job records. R2 remains
  the source of truth for completed audiobook artifacts. D1's FTS5 support is
  documented at <https://developers.cloudflare.com/d1/sql-api/sql-statements/>.
  Use one database per environment, with migrations checked into `worker/`.
- Gutenberg IDs and Standard Ebooks edition paths are stable **source IDs**.
  The client submits an ID, never a URL, local path, shell argument, or arbitrary
  model setting. The consumer resolves a stored, allowlisted EPUB URL and
  verifies it is an EPUB before processing.
- v1 generation is owner-only. The web client prompts the owner for a token
  held in session storage; native uses secure device storage. A Worker secret
  validates that token. A separate Worker secret authorizes PC claim, progress,
  completion, and source-index writes. Neither secret is bundled in the app.
  Listening and searching remain public.
- One active job per `(source_id, rendition)` is enforced in D1. Duplicate
  requests return the existing job. An already published edition opens its
  audiobook unless the owner explicitly requests regeneration. Retrying a
  failed job retains its build ID and resumes; explicit regeneration after
  completion creates a fresh build.
- A claimed job has a short renewable lease. A worker that loses its lease stops
  its child pipeline process. Expired leases are reclaimable. All progress and
  completion writes require the current lease token. The same immutable
  `build_id` is reused after a crash, with `--resume` and the existing `run.json`
  input check. A job is complete only after the rendition manifest, section
  artifacts, book pointer, and catalog are published and readable.
- Source metadata and audiobook catalog entries join on `source_id`. Add an
  optional source ID to newly generated book manifests/catalog rows while
  preserving compatibility with existing R2 objects and local EPUB workflows.
  Existing books without an ID need a reviewed one-time backfill; do not guess
  an edition from title/author alone.

## Cost controls

Budget for Cloudflare Workers Paid ($5/month as of September 2026) if indexing
the full Gutenberg catalog at once. Workers Free has 100,000 requests/day; D1
Free has 5 million rows read/day and 100,000 rows written/day. Free Worker
invocations also have a 10 ms CPU limit, so bounded suggestion ranking matters.
An unindexed autocomplete query or job-claim scan can exhaust the D1 read
allowance even with modest traffic. Use indexed lookup, a small result limit,
and a 300 ms client debounce; inspect D1's actual `rows_read`/`rows_written`
metrics after seeding. Run the initial import in bounded batches. See
<https://developers.cloudflare.com/workers/platform/pricing/> and
<https://developers.cloudflare.com/d1/platform/pricing/>.

An idle PC polls every 30–60 seconds with increasing backoff on errors. Thirty
seconds costs 2,880 Worker requests/day. Heartbeats happen only for a running
job. Owner authorization, one active job per edition/rendition, one local
generation at a time, and a retry cap bound accidental GPU/API work.

Use R2 **Standard** for audio. It includes 10 GB-month of storage and free
egress; additional storage is $0.015/GB-month at current rates. At the pipeline's
48 kbps AAC setting, an eight-hour audiobook is about 173 MB of audio before
metadata and retained builds. Track total retained builds, since regenerating
the same title adds storage. See <https://developers.cloudflare.com/r2/pricing/>.

Kokoro TTS and WhisperX run on the PC. The explicit voice in the first slice
bypasses the registry LLM call, and Kokoro does not use performance-direction
LLM calls; this path can avoid per-book model API fees. Automatic narrator
selection and other engines may call the configured paid provider. Log model
token usage and the PC's measured generation time before estimating their
per-book cost. Electricity is `average PC kW × runtime hours × local $/kWh`.

## Bot and abuse controls

The current Worker only exposes public reads; it does not yet rate-limit them
and cannot create generation jobs. The following controls belong in the new
implementation:

1. **Public search:** use a Worker rate-limiting binding keyed by route and
   client IP, initially returning HTTP 429 after about 60 autocomplete requests
   per minute per IP. Require 2–80 query characters, cap suggestions at 10,
   use indexed D1 search, and briefly cache identical public queries. Make
   audio/range routes a separate, more generous category so normal playback
   is not throttled. Adjust thresholds from measured traffic.
2. **Job creation:** require an owner credential on every create, retry, and
   regeneration request; reject invalid credentials before a job write. Store
   the verifier as a Worker secret, never in the public client bundle or logs.
   Apply a separate rate limit to authentication failures. Enforce, with atomic
   D1 writes, one active job per source/rendition, at most three queued jobs,
   an initial owner budget of two generation starts per day including retries,
   and at most three attempts per job. Return the existing job for duplicate
   requests. Only accept known source IDs and allowed engine/voice choices.
   The D1 quota is the authoritative GPU cost cap.
3. **PC access:** require a distinct, revocable consumer credential for claim,
   heartbeat, progress, source sync, and finish. Keep it only on the PC.
   Require a valid lease token for job updates. No inbound PC port is exposed.
4. **Edge defense:** the Worker limiter protects D1, but rejected requests still
   invoke the Worker. If the API moves from its current `workers.dev` address to
   a Cloudflare custom domain, add a zone WAF rate-limiting rule to reject floods
   before Worker invocation. Return 429/block for JSON API traffic; an
   interactive challenge can break native clients and the PC consumer. The
   Worker rate-limiting binding is per Cloudflare location and eventually
   consistent, so never use it as the only quota. See
   <https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/>
   and <https://developers.cloudflare.com/waf/rate-limiting-rules/>.

CORS, hiding the Generate button, and client-side debounce do not authorize
requests. Turnstile is optional if generation later opens beyond the owner;
its token must be verified by the Worker via Siteverify, and it does not
replace authentication or job quotas. See
<https://developers.cloudflare.com/turnstile/get-started/server-side-validation/>.

## Proposed contracts to freeze before coding

### Source index

`source_books` stores `source_id`, `source`, canonical edition/page URL, EPUB URL,
title, authors, language, EPUB availability, `updated_at`, and optional published
`author_slug`/`title_slug`. Ingestion upserts metadata from Gutendex and Standard
Ebooks. Only entries with a known downloadable EPUB appear in search. Record the
last successful source refresh so an operator can see when the index is stale.

`GET /api/v1/source-books?q=&limit=` returns ranked suggestions with source ID,
title, author, source, and one of `ready_to_generate`, `queued`, `running`,
`failed`, or `available`, derived from jobs and published audiobook metadata.
An available result carries the existing book route slugs. Query
length is bounded; queries shorter than two characters return no suggestions.
Ranking favors exact title, title prefix, author prefix, then word matches.
For queries of at least three characters, a bounded candidate set gets typo
scoring so `alcie` can suggest *Alice*. Debounce client requests and discard
stale responses; do not invoke a model for each keystroke. Preserve
`GET /catalog` as the generated-audiobook browse endpoint.

### Jobs and authorization

`generation_jobs` stores a random job ID, source ID, engine, voice, rendition,
fresh 16-hex build ID, state, stage, completed/total sections when known,
timestamps, attempt count, lease token and expiry, concise error code/message,
and eventual book slugs. Keep a database constraint for at most one active job
per source/rendition. Treat voice/engine options as a server-side allowlist.

All new HTTP shapes must be defined in Zod route schemas so
`/api/v1/openapi.json` is the contract:

| Method and route | Caller | Behavior |
| --- | --- | --- |
| `GET /api/v1/source-books` | Public | Ranked downloadable-book suggestions and current availability. |
| `POST /api/v1/generation-jobs` | Owner | Create or return the active job for a source ID and allowed rendition; require an explicit `regenerate` flag for an available book. |
| `GET /api/v1/generation-jobs/:id` | Owner | Current state, stage, section counts, error, and completed book link. |
| `POST /api/v1/generation-jobs/:id/retry` | Owner | Requeue a failed job with the same build ID. |
| `POST /api/v1/internal/generation-jobs/claim` | PC | Atomically lease one eligible queued/expired job, or return no work. |
| `POST /api/v1/internal/generation-jobs/:id/heartbeat` | PC | Renew a matching lease. |
| `POST /api/v1/internal/generation-jobs/:id/progress` | PC | Update stage/counts under a matching lease. |
| `POST /api/v1/internal/generation-jobs/:id/finish` | PC | Mark success or failure under a matching lease; verify R2 before success. |
| `POST /api/v1/internal/source-books/sync` | PC | Upsert validated source metadata in bounded batches. |

Terminal states are `completed` and `failed`; `queued` and `running` are active.
Within `running`, stage values such as `download`, `parse`, `direction`,
`synthesis`, `alignment`, `encode`, and `upload` are informational. Expose section
counts where trustworthy; avoid a fabricated percent. Use an atomic D1 claim
statement and a lease-token compare on every subsequent mutation. Protect all
state-changing routes, bound request sizes, and use `Cache-Control: no-store`
for job responses. Expand CORS for the new authenticated POSTs and preflights.

### PC process and publication

The consumer starts with a Windows command using the **root** `.venv`, checks
GPU/ffmpeg/R2 and its Worker credentials, and defaults to one simultaneous job.
It polls with backoff when idle, handles Ctrl-C by stopping the child cleanly,
and resumes any reclaimed job under its original build ID. It downloads the
exact indexed edition once, records its hash, then calls an exact-book pipeline
entry point rather than the existing broad `--author`/`--book` search, which can
process multiple matches. Keep the source identity through local artifacts,
book manifest, and catalog generation. Add structured stage/section events at
the pipeline boundary so progress does not rely on parsing human log text.

Before expensive work on a reclaimed job, inspect R2 for a fully published
matching build. If it exists, reconcile the job to `completed`. On success,
verify the final manifest points to the job's build and its expected section
objects exist. If validation fails, leave the job failed with a useful error.

## Work packages and order

The integrator owns the contract, shared files, and final checks. After package
0 freezes the schemas and file ownership, packages 1, 2, and 3 can run in
parallel in separate worktrees. Package 4 consumes their interfaces. Merge and
run the end-to-end gate only after the independent tests pass.

| Package | Owner / primary files | Deliverable | Depends on |
| --- | --- | --- | --- |
| **0. Contracts and docs** | Integrator: root `AGENTS.md` flow, `worker/CLAUDE.md`, `worker/docs/openapi.md`, `client/CLAUDE.md`, `pipeline/AGENTS.md` and affected step docs; shared Worker D1/rate-limit bindings, route mount, and CLI registration | Confirm source IDs, job states, API schemas, R2 metadata, CLI/event shape, and route-specific abuse budgets. Reconcile the known stale chapter references in `worker/CLAUDE.md` and `client/CLAUDE.md` before touching those components. Reserve distinct D1 migration numbers for packages 1 and 2. | None |
| **1. Source discovery** | Search agent: `pipeline/src/openshelf/scrapers/`, a dedicated source-index module; a distinct `worker/` D1 migration and search route/tests | Exact source IDs, initial import/refresh, ranked bounded suggestions, search 429s, reviewed existing-book link. Gutenberg first, then Standard Ebooks. | 0 |
| **2. Job control** | Worker agent: separate job routes, auth/lease helpers, distinct D1 migration/tests | Owner creation/status/retry and PC claim/heartbeat/progress/finish with atomic deduplication, daily/pending quotas, failed-auth throttling, and lease expiration. | 0 |
| **3. PC runner** | Pipeline agent: dedicated consumer and exact-book process modules, CLI/docs/tests | Preflight, exact EPUB acquisition, structured events, lease renewal, resume and publish verification. Use a stub Worker API while package 2 is in flight. | 0 |
| **4. Client** | Client agent: `client/app/`, `components/`, `lib/api.ts`, `types.ts`, tests | Autocomplete, exact edition selection, owner token entry, generate/retry, status, and book opening. | 1, 2 |
| **5. Integration and operations** | Integrator: shared contracts, README/Windows setup, staging bindings/secrets, smoke scripts | One real queued book proceeds through PC generation to playable section sync; deployment and recovery instructions. | 1–4 |

The integrator wires shared `worker/src/index.ts`, `worker/src/types.ts`,
`worker/wrangler.toml`, and `pipeline/src/openshelf/pipeline/books.py` after the
parallel packages land; parallel agents should not edit those files. Each
package follows the repo's docs-first rule: spot-check current docs against
current code, update the relevant spec first, then implement. A newly found
doc/code conflict is surfaced for a decision before that region is edited.
Keep route shapes in Zod; do not hand-maintain an OpenAPI copy.

## Acceptance gates

1. Source index contains a selectable Gutenberg edition without audio. Search
   suggests it for title, author, prefix, and a common typo. Already published
   books offer Open, and a queued/running book shows its job. Repeated public
   search requests receive 429 before an expensive D1 query.
2. Two simultaneous requests for the same source/rendition create one active
   job. A client cannot submit an arbitrary EPUB URL, engine, or voice. Requests
   without owner credentials cannot start or retry jobs. A flood of create and
   retry requests cannot exceed the D1-enforced daily and pending-job caps;
   expired or revoked credentials fail closed.
3. With the PC stopped, the job stays queued. Starting the consumer claims it
   without inbound network configuration. A second consumer cannot claim it
   while the lease is healthy.
4. Kill the consumer during generation. After lease expiry, restart it and
   resume the **same** build. Repeated progress/finish calls and a lost network
   response do not create a second build or mark an incomplete build successful.
5. When the job completes, the catalog and book routes expose its rendition and
   build, section audio streams, and word highlighting works in the reader.
   An upload or alignment failure appears as a recoverable failed job.
6. Run `npm run typecheck`, `npm test`, Python offline tests, GPU preflight in the
   root `.venv`, and a staging end-to-end smoke test. Verify generated OpenAPI
   paths and local/production D1 migrations. Do not claim real synthesis is
   verified from mocked tests alone.

## Release sequence

Deploy additive D1 schema and read-only search first; seed and inspect its
index. Next deploy the authenticated job routes, then run the PC consumer in
staging with one small book. Enable the client Generate control after the
staging acceptance gates pass. Existing `/catalog`, book, section, and audio
routes stay compatible throughout. Keep the old client usable if generation is
disabled; failed jobs remain inspectable and retryable.

The first live release can limit creation to the owner and Kokoro. Add Standard
Ebooks ingestion and additional engine/voice options only after exact-source
handling and resume behavior are demonstrated in staging.
