# Client App Research

Research findings gathered during planning of the OpenShelf cross-platform app.

---

## 1. Expo + React Native (SDK 53+)

### Why Expo over alternatives
- **React Native CLI**: No web support without separate React project. Manual native config.
- **Flutter**: Dart language — no code sharing with existing TypeScript worker.
- **Capacitor/Ionic**: Webview-based — user explicitly rejected webview experience.
- **Expo**: Single JS/TS codebase compiles to native iOS, native Android, and real DOM web. Not webview. Managed workflow handles native config via plugins.

### Expo Router v4 (file-based routing)
- File structure in `app/` directory maps directly to URLs
- `app/index.tsx` → `/`, `app/book/[author]/[title].tsx` → `/book/kafka/the-trial`
- Dynamic segments use `[param]` bracket syntax
- Layouts via `_layout.tsx` files — nest providers, headers, tab bars
- `+not-found.tsx` for 404 handling, `+html.tsx` for custom web HTML shell
- Web URLs = mobile deep links automatically
- Supports typed routes via `expo-router/build/types`

### Project scaffolding
```bash
npx create-expo-app@latest client --template blank-typescript
cd client && npx expo install expo-router react-native-safe-area-context react-native-screens expo-linking expo-constants expo-status-bar
```

---

## 2. Audio Playback: expo-audio

### Why NOT expo-av
- `expo-av` is **deprecated** as of SDK 53 and will be **removed** in SDK 55
- `expo-audio` is the replacement — hooks-based API, better streaming support

### expo-audio API
```typescript
import { useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';

// Create player from URL
const player = useAudioPlayer(audioSource);
const status = useAudioPlayerStatus(player);

// Controls
player.play();
player.pause();
player.seekTo(timeInSeconds);
player.rate = 1.5;  // playback speed
player.volume = 0.8;

// Status fields
status.currentTime   // seconds (number)
status.duration      // seconds (number)
status.playing       // boolean
status.isBuffering   // boolean
status.didJustFinish // boolean — fires when track ends
```

### Background audio
- Requires `expo-audio` background mode configuration in `app.json`:
  ```json
  {
    "expo": {
      "ios": {
        "infoPlist": {
          "UIBackgroundModes": ["audio"]
        }
      }
    }
  }
  ```
- Android: works by default with foreground service
- Lock screen controls: `expo-audio` supports `NowPlayingInfo` metadata

### Audio format update interval
- `useAudioPlayerStatus` fires at a configurable interval
- Default is ~500ms; we want ~100ms for smooth word highlighting
- Set via `player.updateInterval = 100` (milliseconds)

---

## 3. Audio Format Compatibility

### The OGG problem
| Container | iOS Safari | iOS Native | Android | Chrome | Firefox | Safari Mac |
|-----------|-----------|------------|---------|--------|---------|------------|
| Opus/OGG  | NO        | NO         | YES     | YES    | YES     | NO         |
| Opus/M4A  | YES (17+) | YES (17+)  | YES (7+)| YES    | YES     | YES        |
| Opus/WebM | NO        | NO         | YES     | YES    | YES     | NO         |
| AAC/M4A   | YES       | YES        | YES     | YES    | YES     | YES        |

### Solution: Opus in M4A container
- **No re-encoding needed** — same Opus codec, different container
- ffmpeg command change: `-f ogg` → `-f mp4 -strict experimental`
- File extension: `.opus` → `.m4a`
- MIME type: `audio/ogg` → `audio/mp4`
- iOS 17+ covers ~95% of active iOS devices (as of 2025)
- All Android 7+ (API 24+) supports Opus in M4A
- All modern browsers support it

### ffmpeg command
```bash
# Current (OGG)
ffmpeg -i input.wav -c:a libopus -b:a 48k -f ogg output.opus

# New (M4A)
ffmpeg -i input.wav -c:a libopus -b:a 48k -f mp4 output.m4a
```

### File size estimates (Opus 48kbps)
- 1 hour audio ≈ 21 MB
- The Trial (8h) ≈ 168 MB total, ~17 MB/chapter
- Crime and Punishment (21h) ≈ 441 MB total, ~10 MB/chapter
- These are reasonable for streaming; no format change needed for size

---

## 4. NativeWind v5 (Tailwind for React Native)

### What it does
- Write Tailwind classes in React Native components
- On **web**: compiles to real CSS (same as regular Tailwind)
- On **native**: compiles to React Native `StyleSheet` objects at build time
- Not a webview — native styling primitives

### Setup
```bash
npx expo install nativewind tailwindcss react-native-reanimated
```

### Key features
- Platform prefixes: `ios:bg-red-500`, `web:hover:bg-blue-500`
- Dark mode: `dark:bg-gray-900` — but we need 3 modes (light/sepia/dark), so we'll use React Context for theming instead of relying solely on dark mode
- Responsive: `sm:`, `md:`, `lg:` breakpoints work on all platforms
- `className` prop works on all RN components

### Caveats
- v5 requires Metro bundler config changes (covered by `withNativeWind` preset)
- Some complex CSS (gradients, shadows) have limited native support
- `gap` property works on native (Yoga layout engine supports it)

---

## 5. MMKV Storage

### Why not AsyncStorage
- MMKV is **30x faster** than AsyncStorage
- **Synchronous API** — no async/await needed for reads
- Written in C++ with JSI bindings (no bridge overhead)
- Used by WeChat for 1B+ users

### API
```typescript
import { MMKV } from 'react-native-mmkv';

const storage = new MMKV();

// Sync read/write
storage.set('settings.theme', 'sepia');
const theme = storage.getString('settings.theme'); // 'sepia'

storage.set('progress.kafka/the-trial', JSON.stringify({
  chapter: 3,
  audioTime: 142.5,
  updatedAt: new Date().toISOString()
}));
```

### Web fallback
- `react-native-mmkv` v3 has built-in web support using `localStorage`
- No separate implementation needed

### Storage schema for OpenShelf
```
progress:{author}/{title} → { chapter: number, audioTime: number, updatedAt: string }
settings → { theme: 'light'|'sepia'|'dark', fontSize: number, playbackRate: number, syncEnabled: boolean }
```

---

## 6. Audiobook App UX Research

### Libby (by OverDrive)
- **Layout inspiration**: Clean card-based catalog, warm typography
- Book cards: Cover art dominant, metadata below
- Reader: Serif font, cream background option, bottom player bar
- Chapter navigation via dropdown in header
- Progress saved per-book

### Speechify
- **Word highlighting inspiration**: Gold/yellow highlight on active word
- Sentence-level tinting (light background on active sentence)
- Smooth auto-scroll follows narrator
- Speed control prominent (0.5x to 4.5x)
- Text + audio always in sync

### Storytel
- **Player bar inspiration**: Sticky bottom bar, minimal controls
- Large play/pause, forward/back skip buttons
- Progress bar with time labels
- Background: warm gradients based on book cover colors
- Sleep timer feature

### Audible
- Chapter list with durations
- Bookmarking system
- Speed: 0.5x to 3.5x in 0.05 increments
- Clip & share feature
- Stats (listening time)

### Apple Books (audiobooks)
- Clean, minimal player
- Large cover art on player screen
- Skip 15s forward/back (not chapter skip)
- Sleep timer
- Car Play integration

### Design decisions for OpenShelf
- **No cover art** (public domain books rarely have good covers) → typographic cards with deterministic hue from title hash
- **Speechify-style dual highlighting**: word-level (yellow/gold) + chunk-level (subtle background tint)
- **Libby-style layout**: clean, card-based, warm
- **Storytel-style player bar**: sticky bottom, minimal
- **Three themes**: Light (Libby-inspired), Sepia (Kindle-inspired), Dark

---

## 7. Text-Audio Sync Engine

### Alignment data structure
Each word entry from WhisperX alignment:
```json
{
  "word": "arrested",
  "start": 12.45,
  "end": 12.89,
  "chunk_idx": 3,
  "element_id": "p-7"
}
```

### Binary search for O(log n) lookup
Given `currentTime` from audio player, find the active word:
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

### Per-chapter alignment sizing
- Full book alignment can be 7-38 MB (too large for mobile)
- Per-chapter: typically 1K-5K words = 100-500 KB
- Prefetch next chapter while current plays

### Update loop
1. `useAudioPlayerStatus` fires every ~100ms
2. Binary search alignment array → `activeWordIndex`
3. Derive `activeChunkIdx` from `words[activeWordIndex].chunk_idx`
4. Only re-render changed paragraph (React.memo)

---

## 8. React Native Text Rendering Performance

### The Android problem
React Native on Android rebuilds ALL `<Text>` spans when any nested `<Text>` style changes. This is because Android's `SpannableStringBuilder` doesn't support partial updates.

Example: 1000 words in a chapter, each wrapped in `<Text>` for individual highlighting → changing one word's style rebuilds all 1000 spans. At 100ms update interval, this causes visible jank.

### Mitigation: chunk windowing
1. **Paragraph-level memoization**: Wrap each paragraph in `React.memo`. Only the paragraph containing the active word re-renders.
2. **Selective span expansion**: Only the active paragraph + immediate neighbors render word-level `<Text>` spans. All other paragraphs render as plain `<Text>` (single span, no word wrapping).
3. **Max active spans**: ~1,500 word spans active at any time (3 paragraphs worth).

### Auto-scroll implementation
```typescript
// Track paragraph positions
const paragraphPositions = useRef<Record<number, number>>({});

// onLayout callback for each paragraph
<View onLayout={(e) => {
  paragraphPositions.current[chunkIdx] = e.nativeEvent.layout.y;
}}>

// When active chunk changes, scroll
scrollViewRef.current?.scrollTo({
  y: paragraphPositions.current[activeChunkIdx] - 100, // 100px offset from top
  animated: true
});
```

### Manual scroll detection
- `onScrollBeginDrag` → set `userScrolling = true`, clear auto-scroll
- After 5 seconds of no manual scroll → resume auto-scroll
- `onMomentumScrollEnd` can also be used as a signal

---

## 9. Theme System

### Three modes needed
React Native only has binary `useColorScheme()` (light/dark). We need three modes: light, sepia, dark.

### Implementation: React Context
```typescript
type Theme = 'light' | 'sepia' | 'dark';

const ThemeContext = createContext<{
  theme: Theme;
  setTheme: (t: Theme) => void;
  colors: ThemeColors;
}>(...);
```

### Color palettes
```
Light:  bg=#FFFFFF  text=#1A1A1A  highlight=#FFD700  chunkTint=#F5F5DC
Sepia:  bg=#F4ECD8  text=#5B4636  highlight=#E8A317  chunkTint=#EDE0C8
Dark:   bg=#1A1A2E  text=#E0E0E0  highlight=#FFD700  chunkTint=#2A2A3E
```

---

## 10. Expo Web Export

### Static export for Cloudflare Pages
```bash
npx expo export --platform web
```
- Produces `dist/` directory with static HTML/CSS/JS
- Can be deployed to Cloudflare Pages, Vercel, Netlify, or any static host
- Client-side routing handled by Expo Router

### Web-specific considerations
- `+html.tsx` for custom HTML shell (meta tags, fonts)
- Keyboard shortcuts (Space=play/pause, ←/→=seek) via `useEffect` + `addEventListener`
- Reading column max-width: 680px (optimal 55-75 chars/line for readability)
- `<meta>` tags for SEO: title, description per book

---

## 11. Mobile Build & Distribution

### EAS Build (Expo Application Services)
```bash
npx eas-cli build --platform ios
npx eas-cli build --platform android
```

### iOS considerations
- Requires Apple Developer account ($99/year)
- Background audio needs `UIBackgroundModes: ["audio"]` in `app.json`
- Minimum iOS 17 for Opus/M4A support (covers ~95% devices)

### Android considerations
- Can distribute via APK or Play Store
- Foreground service needed for background audio (handled by expo-audio)
- Minimum API 24 (Android 7) for Opus/M4A
