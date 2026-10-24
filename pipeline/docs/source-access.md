# Source access, reading caches and Chinese editions

## Owner documents

Verified owners can upload text PDFs, DOCX and UTF-8 TXT, or paste text, through
the owner document form. Legacy `.doc`, scanned/image-only or encrypted PDFs,
and arbitrary binary files fail explicitly; OCR is not provided. Inputs are
bounded to 16 MiB, 2 million extracted characters, 1,000 PDF pages, and DOCX
ZIP limits of 5,000 entries/64 MiB expanded. The main DOCX XML is capped at
16 MiB and 200,000 elements before narration. Extraction retains PDF page order,
DOCX paragraph/table order, and TXT paragraph order; layout, images and footnotes
are not narration content.
PDF decompression streams are capped at 16 MiB and content parsing has an
operation budget; all document conversion runs in a 60-second subprocess after
a lease renewal. Timed-out extraction stops its complete process tree.
Owners choose English or Mandarin and must explicitly
confirm distribution rights and that the finished audiobook and annotated EPUB
will be public. This is operator authorization, never a claim of U.S. public domain.

`POST /admin/uploads` accepts bounded raw document bytes with metadata validated
by its Zod route schema. Originals are stored at `owner-uploads/{uuid}/original.{format}`
and downloaded only through a PC-authenticated route. Hidden `ownerupload:{uuid}`
source rows have no discovery tokens and are excluded from public search; upload
listing and generation/status requests require the verified owner. The PC must
advertise document-ingestion capability before claiming these jobs. It verifies
the original SHA-256, extracts text, creates a deterministic ordered EPUB carrying
owner-supplied title/author/language and identity, and uses the existing local
Kokoro/Qwen/WhisperX build pipeline with source `local`. Existing start/queue caps,
leases, retries and atomic publication checks apply. Extraction failures occur
before synthesis and are shown to the owner. Original bytes are never served by
public source EPUB routes. `pipeline/owner_documents.py` owns extraction and EPUB
construction. The client owner form follows the job and links to published audio.
Deleting a publication or pruning its missing R2 publication retains the private
original and owner upload entry. Jobs retired with stage `deleted` remain audit
history and are excluded from the entry's latest-job pointer, so the owner can
start a fresh generation rather than following an exhausted retired job.
Automatic source and owner-document consumers require complete audio: skipped
body chunks fail the build before publishing a completion pointer or reporting
job success. They retain validated checkpoints for repair/retry.

Operators may set `OPENSHELF_ENGLISH_PYTHON` to an isolated healthy Python >=3.12
interpreter for standard English Kokoro jobs. The consumer probes Kokoro,
WhisperX >=3.8.4 and pipeline dependencies before advertising standard-English
capability. A broken configured runtime leaves those jobs queued; Mandarin and
expressive capability remain independent. Unconfigured operators retain their
current interpreter and fixed engine/voice. Expressive jobs retain their existing
runtime. This option isolates known native-library failures without changing
rendition identity or build settings.

The PC imports reading copies independently of audio. Indexed Read now serves
validated R2 EPUBs without upstream requests. Publication deletion retains them.
Source search reads the current D1 index on every request, with no result cache
and no upstream website/API fallback. Catalog imports refresh our searchable
collection separately; missing editions must be imported before discovery.
Legacy unindexed Standard Ebooks links retain the exact-file reading fallback.

## Gutenberg

Manual `books download/process` resolves source IDs from exact EPUB locators,
uses the same bounded source downloader and OPF rights/identity checks, and
names local files with source-specific suffixes to avoid edition collisions.
Standard Ebooks uses its exact-file continuation; no cookie is fabricated.
The local fuzzy lookup rejects empty ASCII author/title slugs instead of matching
an unrelated file when given Chinese-only selectors; use an exact EPUB path.

IDs remain `gutenberg:<number>`. Official bulk CSV/RDF records are joined by ID,
with `--language en|zh|all` (default all). Both RDF and EPUB OPF IDs must match;
both rights statements equal `Public domain in the USA.`. EPUB identifiers accept
only exact HTTP/HTTPS `www.gutenberg.org/ebooks/<id>` or legacy
`www.gutenberg.org/<id>` forms for that numeric ID, with no query/fragment.
Downloads map legacy
website URLs to their exact generated variant at the officially listed HTTPS
mirror `https://gutenberg.pglaf.org/cache/epub/<number>/...`. No book downloads
request the human website. `OPENSHELF_GUTENBERG_MIRROR_DIR` optionally names a
local generated collection populated through official rsync. Unknown hosts,
query strings and cross-edition redirects fail. Rights checks use bulk RDF or
mirror RDF, never website scraping.

## Wikisource

`books sync-catalog --source wikisource --editions <json>` takes a curated JSON
array: `title`, `author`, `language` (`en`/`zh`), `publication_year` (the exact
edition including any translation, at most 1929), `root`, and ordered `chapters`.
Each page has `title`, positive `page_id` and `revision`. MediaWiki
`action=parse&oldid=...` must return matching main-namespace IDs/revisions/titles.
The root must have explicit U.S. PD templates (`PD-1923`, `PD-US-expired`,
`PD-old-100-1923`, `PD-old-100-expired`); local PD or site CC BY-SA is insufficient.
Copyright warnings fail closed. Chapters match root `ws-summary` links in DOM
order; standalone works use root as their single chapter. Missing/ambiguous TOCs,
duplicates and redirects fail. API requests use maxlag=5, a descriptive User-Agent,
two-second spacing, bounded pages/responses and no retries on 403/429/maxlag.
The total rendered HTML snapshot is bounded to 64 MiB before EPUB construction.

Identity is `wikisource:<language>:<root-id>:<root-revision>:<snapshot-hash16>`;
the snapshot includes every chapter revision and HTML hash. EPUB OPF identifier
is `urn:openshelf:<source_id>`, and its spine follows the chapter list without
sorting. Original script/text is retained; wiki chrome/navigation is excluded.
EPUBs embed attribution and ordered revision provenance. `epub_url` stores the
root permanent revision locator; downloads require its validated cache.

## Validation, storage and API

All real imports validate archive limits/CRC, exact OPF identity/rights, supported
language, nonempty parsed text, 32 MiB compressed and 64 MiB expanded reader limits.
Import provenance includes `source_url`, `rights_url`, `rights_statement`,
`rights_sha256`, `verified_at`, and ordered Wikisource page snapshots when relevant.
Dry runs inspect metadata/API pages without writes. Cached editions are checked
before downloading. HTTP 403/429 stops with nonzero status and retains completed
copies. Invalid copies are skipped; failures are reported with nonzero status.

R2 keys are `source-epubs/{provider}/{sha256(source_id)}/book.epub` for `gutenberg`,
`standardebooks`, `wikisource`; existing Standard Ebooks keys stay. D1 adds
`language`, JSON `provenance`, nullable `epub_sha256`. Old rows default to English
without cache proof. Authenticated sync accepts bounded provenance/language;
metadata refresh preserves cached provenance/language. Upload accepts every
indexed provider. The PC is the trusted rights/EPUB validator; Worker checks byte
bounds/ZIP magic, stores SHA-256/source ID in R2 metadata and conditionally creates
the copy. Different bytes for an existing ID return 409. Search exposes language
and cache availability and accepts a language filter. Wire shapes are Zod schemas.

GET/HEAD serves cache first, with or without inline. Inline misses return 502;
legacy direct Gutenberg downloads redirect to the approved mirror. Wikisource
misses never redirect to HTML. Consumer uses the cache first and verifies hash,
embedded rights/identity and language.
The PC declares its descriptive OpenShelf User-Agent on cache GETs as well as
authenticated API calls; it never impersonates a browser or forwards credentials
to public download/upstream URLs. Cache redirects remain rejected.
Legacy uncached Gutenberg/Standard Ebooks
jobs may use approved upstream access. Wikisource requires the cache. Standard
jobs select English `kokoro-af-heart` or Chinese `qwen3-serena`. Expressive
jobs remain English-only; unsupported combinations fail before reserving a start.
Queue caps, leases and build isolation stay. ASCII publication keys use stable
source suffixes even for Chinese-only titles/authors.

## Chinese text and models

Published-book discovery also uses D1: `published_books` stores the current public
title/author, default rendition/build and catalog metadata, with canonical Chinese
search fields. `GET /catalog` reads this table with SQL filtering/pagination and
`no-store`; it does not read `catalog.json` or scan R2 during a public search.
The PC-authenticated `POST /internal/catalog/sync` accepts at most 10 publication
keys and derives each row from its actual current R2 book/rendition manifests.
Submitted title/build metadata is never trusted. Successful jobs update this
index before reporting completion; owner deletion removes the row atomically
with publication-pointer cleanup. Deletion guards exclude interrupted deletions
from discovery. `catalog.json` remains an operator audit artifact; job
completion verifies actual build manifests/audio and the D1
publication row rather than requiring a matching R2 catalog snapshot. Manual
books are indexed by `ops catalog` as well as source jobs. Every full catalog
refresh then calls PC-authenticated `/internal/catalog/reconcile`, using keyset
cursors to examine at most 10 existing D1 rows per request, even when R2 has no
publications. It retains valid manifests, reindexes manual pointer changes and
removes only missing/incompatible publications after rechecking R2 object ETags.
Active jobs and deletion guards prevent pruning. A per-row revision prevents a
concurrent upsert from being deleted; transient storage errors never prune rows.
Ten-row sync/reconciliation pages stay within the Workers Free D1 query budget.
Pruning atomically clears the matching source publication pointers and retires
terminal jobs from retry, preserving source reading caches/provenance. Source
search omits retired `deleted` jobs so a new Read now request can queue fresh
audio after an out-of-band publication removal or owner deletion.

Apply migrations `0009_published_catalog.sql` and `0010_catalog_revision.sql`,
deploy the Worker, then backfill all
existing R2 publications through `ops catalog --api-base <Worker /api/v1 URL>`
with `OPENSHELF_PC_TOKEN` before deploying the client. Catalog refresh requires
that explicit API base (or `OPENSHELF_API_BASE`) and a PC token; failed sync is a
visible nonzero error. Consumers pass their API base to processing children.

Automatic languages are `en` and `zh`; Chinese BCP-47 variants normalize to `zh`
for models while source script stays. Unicode source indexing adds Han bigrams
and OpenCC script-normalized tokens. Search expands Chinese queries into original,
simplified and standard/Taiwan/Hong Kong traditional forms, so existing indexed
editions remain discoverable without reimport. Each variant first looks up the
full query token prefix in the index; only an empty result falls back to a Han
bigram lookup for embedded substrings. Each lookup is bounded to 80 candidates,
so an exact full title is not lost behind earlier two-character-prefix matches.
Ranking compares canonical simplified title/author,
deduplicates edition IDs and returns the original source script. Original EPUB
bytes, provenance, identity and narration text are never converted. Indexing
reserves token space for both title and author. Search responses use `no-store`,
including errors; newly imported editions, cache availability and job/publication
state are visible on the next request. Search never requests upstream services;
the API's own abuse limiter still bounds public traffic.
`pipeline/language.py` supplies normalization and offset-preserving units: one
Han character or one non-Han word. Chinese chunks have a 200-unit budget and split
at Chinese sentence/clause boundaries or hard boundaries without adding spaces.
Parser counts, alignment weights/coverage and synthesis budgets use these units.
WhisperX `zh` yields character timings. Client spans locate units in original
text, preserving punctuation, whitespace and unaligned text.
Adjacent Latin character timings from the Chinese model are combined into the
original Latin word, using its first/last aligned character times.

The DAG propagates OPF language to engine, aligner, director and resume context
(existing English `a` stays; Chinese uses `zh`), and localizes Chinese credits.
Kokoro uses `lang_code='z'` with eight Mandarin voices; English casting excludes
them. Unsupported engine/voice/language pairs fail before synthesis. G2P for
optional Kokoro needs `misaki[zh]`. Mandarin defaults to the Qwen3 adapter in
`engine-knowledge-base.md`; existing Kokoro builds retain their identity.
Offline tests use fakes; integration QA exercises real synthesis and alignment.

## Import and rollout

The regression gate combines the complete offline Python suite with Worker
integration tests and a real API browser suite. Its Chinese fixture has chapter
ordinals deliberately out of numeric order so EPUB spine order is observable.
The browser reads the cached EPUB with upstream blocked, follows a real queued
job, and opens published section data and AAC audio from local R2. English is a
control case. Playback checks preserve punctuation and verify highlighting,
word seeking, section advance and build isolation. A fixture producer supplies
PC import/audio/alignment artifacts in CI; the offline pipeline suite separately
checks their validation. A short real Qwen/WhisperX run remains a release check.
Imported Gutenberg reading order follows the EPUB spine; the current legacy
Gutenberg 24264 EPUB does not provide verified 120-chapter navigation. It is not
used as evidence for that stronger guarantee.

Apply `worker/migrations/0007_source_languages.sql` to each target D1 database
before deploying the updated Worker. Update the pipeline dependencies from
`pipeline/requirements.txt` (or install the pipeline package), deploy the client,
and restart consumers with this checkout. Existing Standard Ebooks reading caches
remain usable; previously uncached source rows gain a validated hash on import.
No audio generation is needed to populate the reading cache.

With `OPENSHELF_PC_TOKEN` set, inspect a bounded Chinese Gutenberg import first:

```text
openshelf-pipeline books sync-catalog --catalog download/pg_catalog.csv.gz --rights-archive download/rdf-files.tar.bz2 --language zh --max-books 50 --api-base <api-base> --dry-run
```

For Wikisource, prepare the curated revision manifest described above:

```text
openshelf-pipeline books sync-catalog --source wikisource --editions download/wikisource-editions.json --language zh --max-books 50 --api-base <api-base> --dry-run
```

Review the metadata/rights results, then remove `--dry-run` to validate and cache
the copies. `<api-base>` includes `/api/v1`. These commands never queue audio.
Use a short Mandarin integration run to check installed G2P, voice quality and
the Chinese WhisperX model before unattended audio production.

## References

- [Gutenberg robot access](https://www.gutenberg.org/policy/robot_access.html)
- [Official mirrors](https://www.gutenberg.org/MIRRORS.ALL)
- [Rsync instructions](https://www.gutenberg.org/help/mirroring.html)
- [MediaWiki parse API](https://www.mediawiki.org/wiki/API:Parsing_wikitext)
- [API etiquette](https://www.mediawiki.org/wiki/API:Etiquette)
- [Kokoro voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)
- [WhisperX alignment](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py)
