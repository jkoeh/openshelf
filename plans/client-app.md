# Plan: OpenShelf Cross-Platform App (Web + iOS + Android)

## Context

OpenShelf has a working Cloudflare Worker API (7 endpoints) serving AI-narrated public domain audiobooks from R2. There is no frontend. We need a cross-platform app where users can browse the catalog, pick a book, and read along while listening — with Speechify-style word-level highlighting and Kindle Whispersync-style tap-to-seek. Design inspiration: Libby (layout), Speechify (word highlighting), Storytel (player bar).

**Key constraint:** User wants native iOS/Android (not webview) + web, with maximum code reuse.

---

## BLOCKER: Audio Format Change (Opus/OGG → Opus/M4A)

**iOS does not support the OGG container.** The pipeline currently produces `.opus` files in OGG containers (`audio/ogg`). This works on web and Android but **will not play on iOS at all**.

**Fix:** Change ffmpeg container from OGG to MP4/M4A. Opus codec stays the same (no re-encoding). Opus in M4A works on iOS 17+ (95%+ of devices), all Android 7+, and all modern browsers.

**Files to change:**
- `pipeline/src/openshelf/pipeline/encoder.py` — ffmpeg output flag: `-f ogg` → `-f mp4` (M4A), file extension `.opus` → `.m4a`
- `pipeline/src/openshelf/pipeline/r2.py` — content type: `audio/ogg` → `audio/mp4`, file filter: `.opus` → `.m4a`
- `pipeline/src/openshelf/config.py` — update any format references
- `worker/src/routes/audio.ts` — content type: `audio/ogg` → `audio/mp4`
- `worker/src/utils/r2-keys.ts` — extension: `.opus` → `.m4a`
- `pipeline/tests/pipeline/test_r2.py` — update all `.opus`/`audio/ogg` assertions
- `plans/r2-api.md` — update format references

**This must be done before the app, as step 0.**

---

## Tech Stack

| Choice | Why |
|---|---|
| **Expo (SDK 53+) + React Native** | Single codebase → native iOS/Android + real DOM web. Not webview. |
| **Expo Router v4** | File-based routing for all 3 platforms. Web URLs = mobile deep links. |
| **expo-audio** | Audio playback (expo-av is deprecated in SDK 53, removed in 55). Hooks API, streaming, seek, playback rate, background playback, lock screen controls. |
| **NativeWind v5** | Tailwind CSS for React Native. Responsive utilities, platform prefixes (`ios:`, `web:`), dark mode. Real CSS on web, StyleSheet on native. |
| **MMKV** | Cross-platform storage (30x faster than AsyncStorage, synchronous API). Falls back to localStorage on web. |
| **React Context** | Three-mode theming (light/sepia/dark). RN only has binary light/dark built-in. |
| **Biome** | Same linter/formatter as worker/ |

---

## Pages & ASCII Mockups

### Route Table (Expo Router file structure)

| File | Web URL | Description |
|---|---|---|
| `app/index.tsx` | `/` | Catalog — browse/search books |
| `app/book/[author]/[title].tsx` | `/book/kafka/the-trial` | Book detail — info, chapters |
| `app/read/[author]/[title].tsx` | `/read/kafka/the-trial` | Reader — reading + listening |
| `app/about.tsx` | `/about` | About page |

**API endpoints used by the client:**
```
GET /api/v1/catalog?q=&page=&limit=            → catalog page
GET /api/v1/books/:author/:title                → book detail page
GET /api/v1/books/:author/:title/chapters/:num  → reader (chapter text)
GET /api/v1/books/:author/:title/alignment/:ch  → reader (per-chapter word alignment, NEW)
GET /api/v1/books/:author/:title/audio/:chapter → reader (audio stream)
GET /api/v1/books/:author/:title/epub           → book detail (download link)
```

---

### Page 1: Catalog (`/`)

**API:** `GET /api/v1/catalog?q={query}&page={page}&limit=20`

```
MOBILE                              DESKTOP (3-col grid)
+----------------------------------+ +---------------------------------------------+
| OpenShelf                [About] | | OpenShelf                           [About] |
+----------------------------------+ +---------------------------------------------+
|                                  | |                                             |
|  [_______ Search books... ____]  | |  [____________ Search books... ___________] |
|                                  | |                                             |
|  +----------------------------+  | |  +-----------+ +-----------+ +-----------+ |
|  |     THE TRIAL              |  | |  | THE TRIAL | | CRIME AND | | BROTHERS  | |
|  |     Franz Kafka            |  | |  | Kafka     | | PUNISHMENT| | KARAMAZOV | |
|  |     10 ch · 8h 0m          |  | |  | 10ch · 8h | | 42ch · 21h| | 100ch·31h | |
|  +----------------------------+  | |  +-----------+ +-----------+ +-----------+ |
|  +----------------------------+  | |                                             |
|  |     CRIME AND PUNISHMENT   |  | +---------------------------------------------+
|  |     Fyodor Dostoevsky      |  |
|  |     42 ch · 21h 30m        |  |
|  +----------------------------+  |
|  [ Load more ]                   |
+----------------------------------+
```

Book cards: Typographic (no cover art). Deterministic background hue from title hash. Serif title, sans-serif metadata.

---

### Page 2: Book Detail (`/book/:author/:title`)

**API:** `GET /api/v1/books/:author/:title`

```
+----------------------------------+
|  [<] OpenShelf                   |
+----------------------------------+
|                                  |
|        THE TRIAL                 |
|        Franz Kafka               |
|                                  |
|     8 hours · 10 chapters        |
|     Source: Project Gutenberg     |
|                                  |
|  [ ▶ Start Listening ]           |  <- primary, navigates to reader w/ autoplay
|  [   Start Reading   ]           |  <- secondary, reader without autoplay
|  [   Download EPUB   ]           |  <- link to /api/v1/.../epub
|                                  |
|  ── Chapters ────────────────    |
|  1. Chapter 1           48 min   |  <- tappable → reader at chapter
|  2. Chapter 2           45 min   |
|  3. Chapter 3           58 min   |
|  ...                             |
+----------------------------------+
```

If progress exists in MMKV → show "Continue from Chapter 3 (2:34)" instead.

---

### Page 3: Reader (`/read/:author/:title`)

**API calls:**
- `GET /books/:a/:t` — manifest (chapter list)
- `GET /books/:a/:t/chapters/:n` — chapter text
- `GET /books/:a/:t/alignment/:ch` — per-chapter word alignment (~100-500 KB)
- Audio via `expo-audio`: URL → `/api/v1/books/:a/:t/audio/:ch`

```
+----------------------------------+
| [<] The Trial   [Ch.3 ▼]   [Aa] |  <- back, chapter picker, settings
+----------------------------------+
|                                  |
|            Chapter 3             |
|                                  |
|    Someone must have been        |
|  telling lies about Josef K.,    |  <- active chunk: subtle bg tint
|  he knew he had done nothing     |
|  wrong but, one morning, he      |
|  was [arrested].                 |  <- active word: yellow highlight
|                                  |
|    Every day at eight in the     |
|  morning he was brought his      |
|  breakfast by Mrs. Grubach's     |
|  cook...                         |
|                                  |
|  (auto-scrolls with narrator)    |
|                                  |
+==================================+
| [<<] [▶] [>>]  ━━●━━━  2:34/48m |  <- sticky bottom player
|              [1x]                |
+----------------------------------+

Settings (overlay triggered by [Aa]):
+----------------------------+
|  Theme: [Light][Sepia][Dark]
|  Font:  [-] 18px [+]
|  Sync:  [ON / OFF]
+----------------------------+
```

**Desktop:** Reading column capped at 680px (optimal 55-75 chars/line). Player bar wider with more controls.

**Key UX:**
- Tap any word → audio seeks to that timestamp
- Auto-scroll follows narrator (pauses on manual scroll, resumes after 5s)
- Chapter auto-advances when audio ends
- Keyboard (web): Space=play/pause, ←/→=seek ±10s

---

## Sync Engine Design

### Data flow
```
expo-audio status.currentTime → binary search alignment → activeWordIndex → Text style update
```

### Alignment data size concern

At ~113 bytes/word in JSON, alignment files scale with book length:
- 60K words (The Trial): ~7 MB
- 200K words (Crime and Punishment): ~22 MB
- 350K words (Brothers Karamazov): ~38 MB

**Solution: Add a per-chapter alignment endpoint to the Worker API.**

New endpoint: `GET /api/v1/books/:author/:title/alignment/:chapter`
- Returns only one chapter's words (typically 1K-5K words = 100-500 KB)
- Worker parses the full `word_alignment.json` from R2 and returns the requested chapter
- Client fetches alignment per-chapter, not per-book
- Prefetch next chapter's alignment while current chapter plays

This requires adding a new route (`worker/src/routes/alignment.ts`) and updating `worker/src/index.ts`.

### Alignment indexing
- Fetch current chapter's alignment on chapter load (~100-500 KB)
- Prefetch next chapter's alignment in background
- Words are pre-sorted by `start` time (server guarantees monotonic order)

### Time-to-word lookup: O(log n) binary search
```typescript
function findWordAtTime(words: WordEntry[], time: number): number {
  let lo = 0, hi = words.length - 1, result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    if (words[mid].start <= time) { result = mid; lo = mid + 1; }
    else { hi = mid - 1; }
  }
  return result;
}
```

### Update mechanism
- `expo-audio`'s `useAudioPlayerStatus` fires at configurable interval (set to ~100ms)
- On each status update, run binary search → update `activeWordIndex` state
- Only the affected `<Text>` components re-render (React.memo on paragraphs)

### Android performance mitigation
React Native has a known issue where updating nested `<Text>` styles on Android rebuilds all spans. Mitigations:
1. **Paragraph-level memoization** — `React.memo` each paragraph, only re-render the paragraph containing the active word
2. **Chunk windowing** — Only expand active chunk + neighbors into per-word `<Text>` spans. Far-away chunks render as plain `<Text>` (no word-level nesting)
3. Limits active word-level DOM to ~1,500 spans max

### Highlighting (dual-layer, like Speechify)
```
Active word:  <Text style={{ backgroundColor: highlightColor }}>arrested</Text>
Active chunk: <View style={{ backgroundColor: chunkTintColor }}>...paragraph...</View>
```
Theme-aware colors via React Context.

### Auto-scroll
- `ScrollView.scrollTo({ y: paragraphY, animated: true })` when active paragraph changes
- Use `onLayout` on paragraphs to track Y positions
- Pause on user manual scroll (detect via `onScrollBeginDrag`), resume after 5s timeout

### Tap-to-seek
- Each word `<Text>` wrapped in `<Pressable onPress={() => player.seekTo(word.start)}>`
- If paused, also starts playback

---

## State Management

**Global (React Context):**
- Theme: `'light' | 'sepia' | 'dark'`
- Reader settings: fontSize, syncEnabled

**Component-local:**
- Catalog: searchQuery, books, page, loading
- Reader: chapterText, alignmentData (cached), activeWordIndex, activeChunkIdx
- Audio: managed by expo-audio hooks (currentTime, duration, playing, rate)

**MMKV storage schema:**
```
progress:{author}/{title} → { chapter: 3, audioTime: 142.5, updatedAt: "..." }
settings → { theme: "sepia", fontSize: 18, playbackRate: 1.0, syncEnabled: true }
```

Progress saves: every 10s while playing + chapter change + app background (`AppState` listener).

---

## Project Structure

New `client/` directory at repo root, following the monorepo pattern (`pipeline/`, `worker/`, `client/`).

```
client/                           # Expo app — web + iOS + Android
  app/                            # Expo Router — routes ONLY
    _layout.tsx                   # Root layout (providers, fonts, theme)
    index.tsx                     # Catalog page
    about.tsx                     # About page
    book/[author]/[title].tsx     # Book detail page
    read/[author]/[title].tsx     # Reader page
    +not-found.tsx                # 404
    +html.tsx                     # Custom web HTML shell

  components/                     # Reusable UI
    BookCard.tsx
    BookCardGrid.tsx
    SearchBar.tsx
    ChapterList.tsx
    ReaderHeader.tsx
    ReadingPane.tsx
    SyncedText.tsx                # Core: word-level sync rendering
    WordSpan.tsx                  # Single highlighted word
    AudioPlayer.tsx               # Sticky bottom player bar
    ProgressBar.tsx               # Seekable audio progress
    SettingsPanel.tsx             # Theme/font/sync overlay
    ChapterDropdown.tsx

  lib/                            # Business logic (pure TS, platform-agnostic)
    api.ts                        # Typed fetch for all API endpoints
    sync-engine.ts                # Binary search, alignment indexing
    storage.ts                    # MMKV wrapper for progress + settings
    format.ts                     # formatDuration(), slug hash → hue

  hooks/                          # Custom React hooks
    useTheme.ts                   # Theme context + provider
    useSyncEngine.ts              # Connects expo-audio status to word index
    useProgress.ts                # Save/restore reading progress

  constants/
    colors.ts                     # Theme palettes (light/sepia/dark)
    config.ts                     # API base URL, defaults

  types.ts                        # CatalogBook, Manifest, Chapter, WordEntry, etc.

  assets/
    fonts/                        # Serif reading font if not using system Georgia

  app.json                        # Expo config
  package.json
  tsconfig.json
  tailwind.config.js              # NativeWind config
  biome.json                      # Copied from worker/
  CLAUDE.md                       # Client-specific conventions
```

---

## Implementation Order (16 steps)

| # | Step | What it produces | How to verify |
|---|------|-----------------|---------------|
| 0a | **Audio format: OGG→M4A** | Pipeline produces `.m4a`, Worker serves `audio/mp4` | Python tests pass, worker tests pass, audio plays in Safari |
| 0b | **Per-chapter alignment endpoint** | `GET /api/v1/books/:a/:t/alignment/:chapter` returns single chapter's words (~100-500 KB vs 7-38 MB) | Worker test: fetch chapter 1 alignment, verify word array |
| 1 | **Scaffold** | `client/` dir with Expo, Router, NativeWind, biome, TS, CLAUDE.md. Update root CLAUDE.md + .gitignore | `cd client && npx expo start` shows blank app on web + mobile |
| 2 | **API client + types** | `lib/api.ts`, `types.ts` — typed fetch for all endpoints | Unit tests with mocked fetch |
| 3 | **Theme system** | Context provider, 3 palettes (light/sepia/dark), MMKV persist | Toggle themes, persists on reload |
| 4 | **Router + page shells** | 4 routes with placeholder content, header nav | Navigate between pages on all platforms |
| 5 | **Catalog page** | Search, book cards with hashed colors, pagination | Browse books, search filters results |
| 6 | **Book detail page** | Manifest display, chapter list, action buttons | View book info, tap chapter → reader |
| 7 | **Reader — text only** | ScrollView with chapter text, chapter nav, theming | Read text, switch chapters, change theme/font |
| 8 | **Audio player** | expo-audio playback, sticky bottom bar, play/pause/seek/speed | Listen to audio, seek, change speed |
| 9 | **Sync engine — core** | Binary search, alignment fetch/cache, activeWordIndex | Unit tests: correct word found at each timestamp |
| 10 | **Sync engine — rendering** | SyncedText + WordSpan, chunk windowing, highlight styles | Words highlight in sync with audio |
| 11 | **Tap-to-seek** | Press word → audio seeks, auto-scroll follows narrator | Tap word, audio jumps; text scrolls with playback |
| 12 | **Progress persistence** | MMKV save/restore, "Continue" button on book detail | Close app, reopen → resumes where left off |
| 13 | **Background audio** | Lock screen controls, background playback | Audio continues when app backgrounded |
| 14 | **About page + polish** | Loading/error/empty states, keyboard shortcuts (web), meta tags | All edge cases handled |
| 15 | **Production build** | Web: static export for Cloudflare Pages. Mobile: EAS Build config | `npx expo export --platform web`, EAS builds |

---

## Monorepo Updates

Root `CLAUDE.md` must be updated to add the `client/` component:
```
client/                 # TypeScript — Expo app (web + iOS + Android)
  app/                  # Expo Router file-based routes
  components/           # Reusable UI components
  lib/                  # Business logic (sync engine, API, storage)
  package.json
  app.json
```

Root `.gitignore` must add: `client/node_modules/`, `client/.expo/`, `client/dist/`

## Key Files Referenced

- `pipeline/src/openshelf/pipeline/encoder.py` — ffmpeg command to change container format
- `pipeline/src/openshelf/pipeline/r2.py` — upload content type and file filter
- `worker/src/routes/audio.ts` — audio content type header
- `worker/src/utils/r2-keys.ts` — audio file extension in R2 key builder
- `worker/fixtures/word_alignment.json` — alignment data shape for sync engine
- `worker/biome.json` — biome config to copy

## Verification

1. `cd client && npx expo start` — runs on web, iOS simulator, Android emulator
2. Point API at local worker: `EXPO_PUBLIC_API_BASE=http://localhost:8787/api/v1`
3. Browse catalog, search, tap a book
4. Start listening → words highlight in sync
5. Tap a word → audio seeks
6. Switch themes → persists on restart
7. Kill app → "Continue from Chapter 3" appears on reopen
8. Background app → audio keeps playing with lock screen controls
9. `cd client && npm test` — all tests pass
10. `cd client && npx expo export --platform web` — static build for deployment
