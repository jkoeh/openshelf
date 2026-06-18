import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { useLocalSearchParams } from "expo-router";
import { List, Type } from "lucide-react-native";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, AppState, Platform, Pressable, ScrollView, Text, View } from "react-native";
import AudioPlayerBar, { nextRate } from "../../../components/AudioPlayerBar";
import ChapterDropdown from "../../../components/ChapterDropdown";
import Header from "../../../components/Header";
import ReadingPane from "../../../components/ReadingPane";
import SettingsPanel from "../../../components/SettingsPanel";
import { useSyncEngine } from "../../../hooks/useSyncEngine";
import { useTheme } from "../../../hooks/useTheme";
import { audioUrl, fetchBook, fetchBookBuilds, fetchChapter } from "../../../lib/api";
import {
  nextAutoplayChapterAfterFinish,
  shouldPlayPendingChapter,
} from "../../../lib/chapter-autoplay";
import { selectRendition } from "../../../lib/renditions";
import {
  getSavedFontSize,
  getSavedPlaybackRate,
  isSyncEnabled as getIsSyncEnabled,
  saveFontSize,
  savePlaybackRate,
  saveProgress,
} from "../../../lib/storage";
import type { BookBuildsResponse, ChapterResponse, Manifest } from "../../../types";

export default function ReaderPage() {
  const {
    author,
    title,
    chapter: chapterParam,
    time: timeParam,
    autoplay,
    rendition: renditionParam,
    build: buildParam,
  } = useLocalSearchParams<{
    author: string;
    title: string;
    chapter?: string;
    time?: string;
    autoplay?: string;
    rendition?: string;
    build?: string;
  }>();
  const { colors } = useTheme();

  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [buildSelection, setBuildSelection] = useState<BookBuildsResponse | null>(null);
  const [buildSelectionLoaded, setBuildSelectionLoaded] = useState(false);
  const [chapterData, setChapterData] = useState<ChapterResponse | null>(null);
  const [pinnedBuild, setPinnedBuild] = useState<{
    chapter: number;
    rendition: string;
    build: string;
  } | null>(null);
  const [currentChapter, setCurrentChapter] = useState(
    chapterParam ? Number.parseInt(chapterParam, 10) : 1,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState(getSavedFontSize);
  const [showChapters, setShowChapters] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [rate, setRate] = useState(getSavedPlaybackRate);
  const [syncEnabled] = useState(getIsSyncEnabled);
  const scrollRef = useRef<ScrollView>(null);
  const initialAutoplayDone = useRef(false);
  const pendingAutoplayChapter = useRef<number | null>(null);
  const handledFinishKey = useRef<string | null>(null);

  // Audio player
  const audioSrc =
    author && title && pinnedBuild?.chapter === currentChapter
      ? audioUrl(author, title, currentChapter, pinnedBuild.rendition, pinnedBuild.build)
      : null;
  const player = useAudioPlayer(audioSrc, { updateInterval: 50, crossOrigin: "anonymous" });
  const status = useAudioPlayerStatus(player);
  const activeRendition = useMemo(
    () => (manifest ? selectRendition(manifest, renditionParam) : null),
    [manifest, renditionParam],
  );
  const requiresBuildSelection = Boolean(
    buildParam &&
      activeRendition &&
      buildParam !== activeRendition.rendition.current_build,
  );
  const activeBuild = useMemo(() => {
    if (!requiresBuildSelection || !buildParam || !activeRendition || !buildSelection) {
      return null;
    }
    return (
      buildSelection.renditions[activeRendition.key]?.builds.find(
        (option) => option.build === buildParam,
      ) ?? null
    );
  }, [activeRendition, buildParam, buildSelection, requiresBuildSelection]);
  const resolvedBuild = buildParam ?? activeRendition?.rendition.current_build;
  const activeChapters = requiresBuildSelection
    ? (activeBuild?.chapters ?? [])
    : (activeRendition?.rendition.chapters ?? []);
  const totalChapters = activeChapters.length;

  // Sync engine — reads player.currentTime directly via rAF, not via status
  const sync = useSyncEngine(
    author,
    title,
    currentChapter,
    player,
    syncEnabled,
    chapterData?.words,
    status.currentTime,
  );

  // Enable background audio and lock screen controls
  useEffect(() => {
    setAudioModeAsync({
      playsInSilentMode: true,
      shouldPlayInBackground: true,
      interruptionMode: "doNotMix",
    });
  }, []);

  // Set lock screen metadata when manifest loads
  useEffect(() => {
    if (!manifest || !status.isLoaded) return;
    const chInfo = activeChapters.find((ch) => ch.number === currentChapter);
    player.setActiveForLockScreen(true, {
      title: chInfo ? `${chInfo.title}` : manifest.title,
      artist: manifest.author,
      albumTitle: manifest.title,
    });
    return () => {
      try {
        player.clearLockScreenControls();
      } catch {
        // Player may already be released by expo-audio during source switches/unmount.
      }
    };
  }, [manifest, activeChapters, currentChapter, status.isLoaded, player]);

  // Keyboard shortcuts (web only)
  useEffect(() => {
    if (Platform.OS !== "web") return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === " ") {
        e.preventDefault();
        if (status.playing) player.pause();
        else player.play();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        player.seekTo(Math.max(0, status.currentTime - 10));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        player.seekTo(Math.min(status.duration, status.currentTime + 10));
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [player, status.playing, status.currentTime, status.duration]);

  // Set playback rate when player loads or rate changes
  useEffect(() => {
    if (player && status.isLoaded) {
      player.setPlaybackRate(rate);
    }
  }, [player, status.isLoaded, rate]);

  // Seek to saved time on initial load (continue from progress)
  const initialSeekDone = useRef(false);
  useEffect(() => {
    if (timeParam && status.isLoaded && !initialSeekDone.current) {
      initialSeekDone.current = true;
      const t = Number.parseFloat(timeParam);
      if (!Number.isNaN(t) && t > 0) {
        player.seekTo(t);
        player.play();
      }
    }
  }, [timeParam, status.isLoaded, player]);

  // Auto-play on first load if autoplay param is set
  useEffect(() => {
    if (
      autoplay === "1" &&
      status.isLoaded &&
      audioSrc &&
      pinnedBuild?.chapter === currentChapter &&
      !initialAutoplayDone.current
    ) {
      initialAutoplayDone.current = true;
      player.play();
    }
  }, [autoplay, status.isLoaded, audioSrc, pinnedBuild?.chapter, currentChapter, player]);

  // Auto-advance to next chapter when audio finishes
  useEffect(() => {
    if (!status.didJustFinish) {
      handledFinishKey.current = null;
      return;
    }

    const transition = nextAutoplayChapterAfterFinish({
      didJustFinish: status.didJustFinish,
      currentChapter,
      totalChapters,
      pinnedChapter: pinnedBuild?.chapter,
      rendition: pinnedBuild?.rendition,
      build: pinnedBuild?.build,
      handledFinishKey: handledFinishKey.current,
      pendingAutoplayChapter: pendingAutoplayChapter.current,
    });

    if (!transition) return;
    handledFinishKey.current = transition.handledFinishKey;
    pendingAutoplayChapter.current = transition.pendingAutoplayChapter;
    setCurrentChapter(transition.nextChapter);
  }, [status.didJustFinish, totalChapters, currentChapter, pinnedBuild]);

  // When chapter changes and audio was playing, auto-play the new chapter
  useEffect(() => {
    if (
      shouldPlayPendingChapter({
        pendingAutoplayChapter: pendingAutoplayChapter.current,
        currentChapter,
        pinnedChapter: pinnedBuild?.chapter,
        isLoaded: status.isLoaded,
        hasAudioSource: Boolean(audioSrc),
      })
    ) {
      player.play();
      pendingAutoplayChapter.current = null;
    }
  }, [currentChapter, pinnedBuild?.chapter, status.isLoaded, audioSrc, player]);

  // Fetch manifest.
  useEffect(() => {
    if (!author || !title) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setManifest(null);
    setBuildSelection(null);
    setBuildSelectionLoaded(false);
    setChapterData(null);
    setPinnedBuild(null);
    fetchBook(author, title)
      .then((data) => {
        if (!cancelled) setManifest(data);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load book");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [author, title]);

  useEffect(() => {
    if (!author || !title) return;
    let cancelled = false;
    setBuildSelection(null);
    setBuildSelectionLoaded(!requiresBuildSelection);

    if (requiresBuildSelection) {
      fetchBookBuilds(author, title)
        .then((data) => {
          if (!cancelled) setBuildSelection(data);
        })
        .catch((e) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : "Failed to load build selection");
            setLoading(false);
          }
        })
        .finally(() => {
          if (!cancelled) setBuildSelectionLoaded(true);
        });
    }

    return () => {
      cancelled = true;
    };
  }, [author, title, requiresBuildSelection]);

  useEffect(() => {
    if (manifest && !activeRendition) {
      setError("No playable rendition found");
      setLoading(false);
    }
  }, [manifest, activeRendition]);

  useEffect(() => {
    if (!manifest || !activeRendition || !requiresBuildSelection || !buildSelectionLoaded) return;
    if (!activeBuild) {
      setError("Selected build not found");
      setLoading(false);
    }
  }, [manifest, activeRendition, requiresBuildSelection, buildSelectionLoaded, activeBuild]);

  // Fetch chapter text when chapter changes
  useEffect(() => {
    if (!author || !title || !manifest || !activeRendition) return;
    if (requiresBuildSelection && !buildSelectionLoaded) return;
    if (requiresBuildSelection && !activeBuild) return;
    if (!resolvedBuild) return;
    setLoading(true);
    setError(null);
    const rendition = activeRendition.key;
    setPinnedBuild({ chapter: currentChapter, rendition, build: resolvedBuild });
    fetchChapter(author, title, currentChapter, rendition, resolvedBuild)
      .then((data) => {
        setChapterData(data);
        scrollRef.current?.scrollTo({ y: 0, animated: false });
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load chapter"))
      .finally(() => setLoading(false));
  }, [
    author,
    title,
    manifest,
    activeRendition,
    currentChapter,
    requiresBuildSelection,
    buildSelectionLoaded,
    activeBuild,
    resolvedBuild,
  ]);

  // Save progress every 10s while playing
  useEffect(() => {
    if (!author || !title || !pinnedBuild || !status.playing) return;
    const interval = setInterval(() => {
      saveProgress(
        author,
        title,
        pinnedBuild.rendition,
        pinnedBuild.build,
        {
          chapter: currentChapter,
          audioTime: status.currentTime,
          updatedAt: new Date().toISOString(),
        },
      );
    }, 10000);
    return () => clearInterval(interval);
  }, [author, title, pinnedBuild, currentChapter, status.playing, status.currentTime]);

  // Save progress on chapter change
  useEffect(() => {
    if (!author || !title || !pinnedBuild || status.currentTime <= 0) return;
    saveProgress(
      author,
      title,
      pinnedBuild.rendition,
      pinnedBuild.build,
      {
        chapter: currentChapter,
        audioTime: status.currentTime,
        updatedAt: new Date().toISOString(),
      },
    );
  }, [author, title, pinnedBuild, currentChapter, status.currentTime]);

  // Save progress when app goes to background
  useEffect(() => {
    const sub = AppState.addEventListener("change", (nextState) => {
      if (nextState !== "active" && author && title && pinnedBuild) {
        saveProgress(
          author,
          title,
          pinnedBuild.rendition,
          pinnedBuild.build,
          {
            chapter: currentChapter,
            audioTime: status.currentTime,
            updatedAt: new Date().toISOString(),
          },
        );
      }
    });
    return () => sub.remove();
  }, [author, title, pinnedBuild, currentChapter, status.currentTime]);

  const handleFontSizeChange = useCallback((size: number) => {
    setFontSize(size);
    saveFontSize(size);
  }, []);

  const goToChapter = useCallback(
    (num: number) => {
      if (totalChapters === 0) return;
      const clamped = Math.max(1, Math.min(num, totalChapters));
      if (clamped !== currentChapter) {
        // If audio was playing, flag to auto-play new chapter
        if (status.playing) {
          pendingAutoplayChapter.current = clamped;
        }
        player.pause();
        setCurrentChapter(clamped);
      }
    },
    [totalChapters, currentChapter, status.playing, player],
  );

  const handlePlayPause = useCallback(() => {
    if (status.playing) {
      player.pause();
    } else {
      player.play();
    }
  }, [player, status.playing]);

  const handleSeek = useCallback(
    (seconds: number) => {
      player.seekTo(seconds);
    },
    [player],
  );

  // Tap-to-seek: press a word → seek audio to that word's start time
  const handleWordPress = useCallback(
    (wordIndex: number) => {
      const word = sync.words[wordIndex];
      if (!word) return;
      player.seekTo(word.start);
      if (!status.playing) {
        player.play();
      }
    },
    [sync.words, player, status.playing],
  );

  const handleRateChange = useCallback(() => {
    const newRate = nextRate(rate);
    setRate(newRate);
    savePlaybackRate(newRate);
    if (status.isLoaded) {
      player.setPlaybackRate(newRate);
    }
  }, [rate, player, status.isLoaded]);

  if (!author || !title) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Error" showBack />
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ color: colors.textSecondary }}>Invalid book URL</Text>
        </View>
      </View>
    );
  }

  const chapterInfo = activeChapters.find((ch) => ch.number === currentChapter);
  const headerTitle = chapterInfo ? `Ch. ${chapterInfo.number}` : "Loading...";

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header
        title={headerTitle}
        showBack
        rightElement={
          <View style={{ flexDirection: "row", alignItems: "center", gap: 16 }}>
            {manifest ? (
              <Pressable onPress={() => setShowChapters(true)} hitSlop={8}>
                <List size={22} color={colors.primary} strokeWidth={2.4} />
              </Pressable>
            ) : null}
            <Pressable onPress={() => setShowSettings(true)} hitSlop={8}>
              <Type size={20} color={colors.primary} strokeWidth={2.4} />
            </Pressable>
          </View>
        }
      />

      {loading ? (
        <ActivityIndicator style={{ flex: 1 }} color={colors.primary} />
      ) : error ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
          <Text style={{ color: colors.textSecondary, fontSize: 16 }}>{error}</Text>
        </View>
      ) : chapterData ? (
        <ReadingPane
          ref={scrollRef}
          chapterTitle={chapterData.title}
          chunks={chapterData.chunks}
          fontSize={fontSize}
          words={sync.words}
          activeWordIndex={sync.activeWordIndex}
          activeChunkIndex={sync.activeChunkIndex}
          onWordPress={handleWordPress}
        />
      ) : null}

      {/* Audio player bar */}
      <AudioPlayerBar
        playing={status.playing}
        currentTime={status.currentTime}
        duration={status.duration}
        playbackRate={rate}
        isLoaded={status.isLoaded}
        onPlayPause={handlePlayPause}
        onSeek={handleSeek}
        onPrev={() => goToChapter(currentChapter - 1)}
        onNext={() => goToChapter(currentChapter + 1)}
        onRateChange={handleRateChange}
        canPrev={currentChapter > 1}
        canNext={currentChapter < totalChapters}
      />

      {/* Modals */}
      {activeRendition ? (
        <ChapterDropdown
          visible={showChapters}
          onClose={() => setShowChapters(false)}
          chapters={activeChapters}
          currentChapter={currentChapter}
          onSelect={goToChapter}
        />
      ) : null}
      <SettingsPanel
        visible={showSettings}
        onClose={() => setShowSettings(false)}
        fontSize={fontSize}
        onFontSizeChange={handleFontSizeChange}
      />
    </View>
  );
}
