# Client — CLAUDE.md

## What This Is

Expo (React Native) cross-platform app — web + iOS + Android. Audiobook reader with word-level text/audio sync.

## Structure

```
app/                        # Expo Router — file-based routes
  _layout.tsx               # Root layout (providers, status bar)
  index.tsx                 # Catalog page
  about.tsx                 # About page
  book/[author]/[title].tsx # Book detail page
  read/[author]/[title].tsx # Reader page
  +not-found.tsx            # 404

components/                 # Reusable UI components
lib/                        # Business logic (API client, sync engine, storage)
hooks/                      # Custom React hooks
constants/                  # Theme colors, API config
types.ts                    # Shared TypeScript types
```

## Stack

- Expo SDK 55, React Native 0.83
- Expo Router v4 (file-based routing)
- expo-audio (audio playback)
- NativeWind v4 (Tailwind for React Native)
- lucide-react-native + react-native-svg (cross-platform SVG icons)
- react-native-mmkv (persistent storage)
- react-native-nitro-modules (required by MMKV v4)
- react-native-worklets (required by Reanimated v4)
- Biome (linting/formatting)

## Commands

All commands run from the **client/** directory.

```bash
# Dev server
npm start                   # starts Expo dev server
npm run web                 # web only

# Type check
npm run typecheck

# Lint
npm run check

# Static web export for Cloudflare Pages
EXPO_PUBLIC_API_BASE=https://openshelf-api.johnkoeh.workers.dev/api/v1 npm run build:web
```

## Conventions

- Biome enforced: tabs, 100 char line width
- Route files in `app/` are thin — delegate to components
- Business logic lives in `lib/` (pure TS, no React imports)
- Hooks in `hooks/` bridge lib logic to React components
- API base URL via `EXPO_PUBLIC_API_BASE` env var (defaults to localhost:8787)
- Web deploys to Cloudflare Pages from `client/dist`. Keep `public/_redirects`
  present so Expo Router deep links fall back to `index.html`.
- Production web deploys are automated by the `openshelf` Cloudflare Pages
  project's Git integration after merges to the configured production branch.
- The catalog's established styling pattern is inline React Native style props;
  keep that pattern for its responsive redesign. NativeWind remains installed
  for components that already use it.
- Catalog discovery uses a mobile-first, text-first library treatment: warm
  light background, navy serif headings, blue accessible actions, comfortable
  touch targets, and bounded centered content on wide screens. Keep sepia and
  dark themes legible. Source edition cards can become two columns on tablet
  and desktop. When a source query has results, do not show the published
  catalog's empty message below them.
- Icons use `lucide-react-native` SVG components. Do not use icon-font packages
  for app UI; web export can render those as missing-glyph squares if the font
  fails to load.
- The book detail route loads `fetchBook` for the backend default rendition/build and
  separately calls the no-store `fetchBookBuilds` selector API. The rendition selector
  is collapsed by default, always shows the selected engine, expands into engine ->
  voice -> upload-time choices, and collapses again after selection. Raw build IDs stay
  internal to URLs, local storage, and progress keys.
- `useSyncEngine` computes active word/chunk inside a `requestAnimationFrame` loop and only setStates when the active word/chunk index changes. It reads `player.currentTime` and consumes inline `words` from the section response; there is no separate alignment fetch.
- The catalog page keeps published-book browsing and adds debounced source
  suggestions. A visitor can request the fixed local Kokoro narration without
  entering a token. The source-search UI explains that the index is limited;
  a missing match is never presented as proof Gutenberg lacks the book. The
  current owner token remains local for cancellation via the PC script. On web,
  the owner signs in with Google for cancel, retry, and regeneration controls.
  The Google ID token lives only in React memory and is sent to the Worker;
  the client never persists it or bundles the local owner key. If the OAuth
  client ID is unconfigured, the admin control explains setup is needed. Paid
  narration direction remains a separate owner-only job mode.
  A published edition stays playable while its latest regeneration job is
  queued, running, or failed; latest job state drives progress and admin controls
  independently of publication availability.
  If the PC rejects an edition above its spoken-word budget, the generation
  status explains that the owner must deliberately raise `--max-words` before
  an admin retry. If official Gutenberg rights cannot be verified as public
  domain in the USA, the status explains the failure without offering public
  retry.
  The PC rechecks rights before synthesis on every attempt, so retry cannot
  override the gate. For the matching edition, the action follows the latest
  relevant job update time: a locally returned job wins over a stale cached suggestion,
  while a fresher suggestion replaces an older local job ID or state.
  Active public job status may be polled while the catalog remains open; show
  the job ID so the owner can cancel it with the local CLI. Public visitors do
  not see retry or credential entry.

## Do NOT

- Import from `expo-av` — it is deprecated. Use `expo-audio` instead.
- Use AsyncStorage — use `react-native-mmkv` instead.
- Disable React Native New Architecture for this app (it must remain enabled for MMKV v4/Nitro + Reanimated v4).
- Create webview wrappers — all UI must be native components.
