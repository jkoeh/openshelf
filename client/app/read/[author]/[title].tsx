import { Ionicons } from "@expo/vector-icons";
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, AppState, Platform, Pressable, ScrollView, Text, View } from "react-native";
import AudioPlayerBar, { nextRate } from "../../../components/AudioPlayerBar";
import ChapterDropdown from "../../../components/ChapterDropdown";
import Header from "../../../components/Header";
import ReadingPane from "../../../components/ReadingPane";
import SettingsPanel from "../../../components/SettingsPanel";
import { useSyncEngine } from "../../../hooks/useSyncEngine";
import { useTheme } from "../../../hooks/useTheme";
import { audioUrl, fetchBook, fetchChapter } from "../../../lib/api";
import { selectRendition } from "../../../lib/renditions";
import {
  getSavedFontSize,
  getSavedPlaybackRate,
  isSyncEnabled as getIsSyncEnabled,
  saveFontSize,
  savePlaybackRate,
  saveProgress,
} from "../../../lib/storage";
import type { ChapterResponse, Manifest } from "../../../types";

export default function ReaderPage() {
  const {
    author,
    title,
    chapter: chapterParam,
    time: timeParam,
    autoplay,
    rendition: renditionParam,
  } = useLocalSearchParams<{
    author: string;
    title: string;
    chapter?: string;
    time?: string;
    autoplay?: string;
    rendition?: string;
  }>();
  const { colors } = useTheme();

  const [manifest, setManifest] = useState<Manifest | null>(null);
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
  const autoplayTriggered = useRef(false);

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
  const activeChapters = activeRendition?.rendition.chapters ?? [];
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
    if (autoplay === "1" && status.isLoaded && !autoplayTriggered.current) {
      autoplayTriggered.current = true;
      player.play();
    }
  }, [autoplay, status.isLoaded, player]);

  // Auto-advance to next chapter when audio finishes
  useEffect(() => {
    if (status.didJustFinish && totalChapters > 0) {
      if (currentChapter < totalChapters) {
        setCurrentChapter((prev) => prev + 1);
        // New chapter will auto-play since didJustFinish means we were playing
        autoplayTriggered.current = true;
      }
    }
  }, [status.didJustFinish, totalChapters, currentChapter]);

  // When chapter changes and audio was playing, auto-play the new chapter
  useEffect(() => {
    if (autoplayTriggered.current && status.isLoaded) {
      player.play();
      autoplayTriggered.current = false;
    }
  }, [status.isLoaded, player]);

  // Fetch manifest once
  useEffect(() => {
    if (!author || !title) return;
    fetchBook(author, title)
      .then(setManifest)
      .catch((e) => {
        setError(e instanceof Error ? e.message : "Failed to load book");
        setLoading(false);
      });
  }, [author, title]);

  useEffect(() => {
    if (manifest && !activeRendition) {
      setError("No playable rendition found");
      setLoading(false);
    }
  }, [manifest, activeRendition]);

  // Fetch chapter text when chapter changes
  useEffect(() => {
    if (!author || !title || !manifest || !activeRendition) return;
    setLoading(true);
    setError(null);
    const build = activeRendition.rendition.current_build;
    const rendition = activeRendition.key;
    setPinnedBuild({ chapter: currentChapter, rendition, build });
    fetchChapter(author, title, currentChapter, rendition, build)
      .then((data) => {
        setChapterData(data);
        scrollRef.current?.scrollTo({ y: 0, animated: false });
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load chapter"))
      .finally(() => setLoading(false));
  }, [author, title, manifest, activeRendition, currentChapter]);

  // Save progress every 10s while playing
  useEffect(() => {
    if (!author || !title || !status.playing) return;
    const interval = setInterval(() => {
      saveProgress(author, title, {
        chapter: currentChapter,
        audioTime: status.currentTime,
        updatedAt: new Date().toISOString(),
      });
    }, 10000);
    return () => clearInterval(interval);
  }, [author, title, currentChapter, status.playing, status.currentTime]);

  // Save progress on chapter change
  useEffect(() => {
    if (!author || !title || status.currentTime <= 0) return;
    saveProgress(author, title, {
      chapter: currentChapter,
      audioTime: status.currentTime,
      updatedAt: new Date().toISOString(),
    });
  }, [currentChapter]);

  // Save progress when app goes to background
  useEffect(() => {
    const sub = AppState.addEventListener("change", (nextState) => {
      if (nextState !== "active" && author && title) {
        saveProgress(author, title, {
          chapter: currentChapter,
          audioTime: status.currentTime,
          updatedAt: new Date().toISOString(),
        });
      }
    });
    return () => sub.remove();
  }, [author, title, currentChapter, status.currentTime]);

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
          autoplayTriggered.current = true;
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
                <Ionicons name="list" size={22} color={colors.primary} />
              </Pressable>
            ) : null}
            <Pressable onPress={() => setShowSettings(true)} hitSlop={8}>
              <Ionicons name="text" size={20} color={colors.primary} />
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
