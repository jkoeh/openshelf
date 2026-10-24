# Source access, reading caches and Chinese editions

The PC imports reading copies independently of audio. Indexed Read now serves
validated R2 EPUBs without upstream requests. Publication deletion retains them.
Unindexed Standard Ebooks suggestions retain the existing direct-reading fallback.

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

Automatic languages are `en` and `zh`; Chinese BCP-47 variants normalize to `zh`
for models while source script stays. Unicode source indexing adds Han bigrams.
Chinese substring queries use these bigrams to obtain a bounded candidate set,
then rank matches against the full title/author. Indexing reserves token space
for both title and author.
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
