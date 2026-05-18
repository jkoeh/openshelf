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
```

## Conventions

- Biome enforced: tabs, 100 char line width
- Route files in `app/` are thin — delegate to components
- Business logic lives in `lib/` (pure TS, no React imports)
- Hooks in `hooks/` bridge lib logic to React components
- API base URL via `EXPO_PUBLIC_API_BASE` env var (defaults to localhost:8787)
- Styling via NativeWind `className` prop — no inline StyleSheet unless necessary
- `useSyncEngine` computes active word/chunk inside a `requestAnimationFrame` loop and only setStates when the active word/chunk index changes. It should use status time as the primary playback clock (with `player.currentTime` fallback) for iOS reliability. The hook consumes the inline `words` array from the chapter response; there is no separate alignment fetch.

## Do NOT

- Import from `expo-av` — it is deprecated. Use `expo-audio` instead.
- Use AsyncStorage — use `react-native-mmkv` instead.
- Disable React Native New Architecture for this app (it must remain enabled for MMKV v4/Nitro + Reanimated v4).
- Create webview wrappers — all UI must be native components.
