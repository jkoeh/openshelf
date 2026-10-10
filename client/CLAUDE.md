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
  source/[id].tsx           # Immediate source EPUB reader + audio creation status
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
- Google owner sign-in uses the public `EXPO_PUBLIC_GOOGLE_CLIENT_ID` from Pages
  production/preview build variables or ignored `client/.env` in local development.
  It must match the Worker's `GOOGLE_CLIENT_ID`; README.md records the configured ID.
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
  internal to URLs, local storage, and progress keys. Book detail offers Read now and
  listening actions; standalone EPUB download buttons are removed from the UI.
- `useSyncEngine` computes active word/chunk inside a `requestAnimationFrame` loop and only setStates when the active word/chunk index changes. It reads `player.currentTime` and consumes inline `words` from the section response; there is no separate alignment fetch.
- The catalog page keeps published-book browsing and adds debounced source
  suggestions. Every indexed edition offers one **Read now** action that immediately
  opens `source/[id]`. That reader fetches the exact EPUB through `?inline=1`
  and independently creates a fixed Kokoro job, or resumes status for an existing
  job. Published editions open the existing reader. Failed/canceled jobs remain
  readable without public retries. Queue/start-limit failures cannot block text.
  EPUB parsing follows the OPF spine and renders text as native components, never
  executing source HTML. The source reader polls jobs every 15 seconds and shows
  an animated owl above text, honoring reduced motion: Nestling (queued, 0%),
  Gathering (download/parse/direction, 25%), Hooting (synthesis/alignment/encode/upload,
  50%), and Ready to soar (completed, 100%). These are stage milestones, not measured
  synthesis percentages. On completion, Start Listening opens the published reader;
  it does not interrupt text reading. Source search carries the latest
  public audio error code so reopening search retains the specific failure.
  a missing match is never presented as proof Gutenberg lacks the book. The
  current owner token remains local for cancellation via the PC script. On web,
  the owner signs in with Google for cancel, retry, and regeneration controls.
  The Google ID token lives only in React memory and is sent to the Worker;
  Owner controls opens a centered modal above the discovery page, with a dimmed
  backdrop, a visible close button, Escape/backdrop dismissal, trapped keyboard
  focus, and focus restored to the trigger on close. The modal fits narrow and
  short screens and scrolls when needed. It asks the signed-out owner to sign in
  with Google, shows loading and verification progress, and offers retry if the
  Google script fails or times out. Unauthorized accounts, server configuration,
  rate limits, and connection failures have distinct messages. The modal closes
  after successful verification; reopening it shows the owner account, guidance
  to manage jobs from search results, and sign-out;
  the client never persists it or bundles the local owner key. If the OAuth
  client ID is unconfigured, the modal clearly says sign-in is unavailable and
  offers Close; deployment setup details belong in README.md. Paid
  owner may request expressive narration as a separate, fixed Chatterbox
  `af_heart` job with batched OpenAI emotion direction on the PC. This action
  is visible only after owner sign-in and clearly identifies the paid OpenAI
  step; it sends no API key, arbitrary prompt, model, engine, or voice. Public
  requests continue to use fixed Kokoro. Job status identifies the selected
  mode so progress remains understandable on mobile, tablet, and desktop. A
  newly completed polled job counts as published when the owner starts an
  expressive regeneration, even if the cached suggestion still says unbuilt.
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
