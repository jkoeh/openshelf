import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, View } from "react-native";
import AudioPlayerBar, { nextRate } from "../../../components/AudioPlayerBar";
import ChapterDropdown from "../../../components/ChapterDropdown";
import Header from "../../../components/Header";
import ReadingPane from "../../../components/ReadingPane";
import SettingsPanel from "../../../components/SettingsPanel";
import { useTheme } from "../../../hooks/useTheme";
import { audioUrl, fetchBook, fetchChapter } from "../../../lib/api";
import {
  getSavedFontSize,
  getSavedPlaybackRate,
  saveFontSize,
  savePlaybackRate,
} from "../../../lib/storage";
import type { ChapterResponse, Manifest } from "../../../types";

export default function ReaderPage() {
  const {
    author,
    title,
    chapter: chapterParam,
    autoplay,
  } = useLocalSearchParams<{
    author: string;
    title: string;
    chapter?: string;
    autoplay?: string;
  }>();
  const { colors } = useTheme();

  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [chapterData, setChapterData] = useState<ChapterResponse | null>(null);
  const [currentChapter, setCurrentChapter] = useState(
    chapterParam ? Number.parseInt(chapterParam, 10) : 1,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState(getSavedFontSize);
  const [showChapters, setShowChapters] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [rate, setRate] = useState(getSavedPlaybackRate);
  const scrollRef = useRef<ScrollView>(null);
  const autoplayTriggered = useRef(false);

  // Audio player
  const audioSrc = author && title ? audioUrl(author, title, currentChapter) : null;
  const player = useAudioPlayer(audioSrc, { updateInterval: 100 });
  const status = useAudioPlayerStatus(player);

  // Set playback rate when player loads or rate changes
  useEffect(() => {
    if (player && status.isLoaded) {
      player.playbackRate = rate;
    }
  }, [player, status.isLoaded, rate]);

  // Auto-play on first load if autoplay param is set
  useEffect(() => {
    if (autoplay === "1" && status.isLoaded && !autoplayTriggered.current) {
      autoplayTriggered.current = true;
      player.play();
    }
  }, [autoplay, status.isLoaded, player]);

  // Auto-advance to next chapter when audio finishes
  useEffect(() => {
    if (status.didJustFinish && manifest) {
      if (currentChapter < manifest.chapters.length) {
        setCurrentChapter((prev) => prev + 1);
        // New chapter will auto-play since didJustFinish means we were playing
        autoplayTriggered.current = true;
      }
    }
  }, [status.didJustFinish, manifest, currentChapter]);

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
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load book"));
  }, [author, title]);

  // Fetch chapter text when chapter changes
  useEffect(() => {
    if (!author || !title) return;
    setLoading(true);
    setError(null);
    fetchChapter(author, title, currentChapter)
      .then((data) => {
        setChapterData(data);
        scrollRef.current?.scrollTo({ y: 0, animated: false });
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load chapter"))
      .finally(() => setLoading(false));
  }, [author, title, currentChapter]);

  const handleFontSizeChange = useCallback((size: number) => {
    setFontSize(size);
    saveFontSize(size);
  }, []);

  const goToChapter = useCallback(
    (num: number) => {
      if (!manifest) return;
      const clamped = Math.max(1, Math.min(num, manifest.chapters.length));
      if (clamped !== currentChapter) {
        // If audio was playing, flag to auto-play new chapter
        if (status.playing) {
          autoplayTriggered.current = true;
        }
        player.pause();
        setCurrentChapter(clamped);
      }
    },
    [manifest, currentChapter, status.playing, player],
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

  const handleRateChange = useCallback(() => {
    const newRate = nextRate(rate);
    setRate(newRate);
    savePlaybackRate(newRate);
    if (status.isLoaded) {
      player.playbackRate = newRate;
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

  const chapterInfo = manifest?.chapters.find((ch) => ch.number === currentChapter);
  const headerTitle = chapterInfo ? `Ch. ${chapterInfo.number}` : "Loading...";
  const totalChapters = manifest?.chapters.length ?? 0;

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header
        title={headerTitle}
        showBack
        rightElement={
          <View style={{ flexDirection: "row", gap: 12 }}>
            {manifest ? (
              <Pressable onPress={() => setShowChapters(true)}>
                <Text style={{ color: colors.primary, fontSize: 14 }}>
                  {currentChapter}/{totalChapters}
                </Text>
              </Pressable>
            ) : null}
            <Pressable onPress={() => setShowSettings(true)}>
              <Text style={{ color: colors.primary, fontSize: 16 }}>Aa</Text>
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
      {manifest ? (
        <ChapterDropdown
          visible={showChapters}
          onClose={() => setShowChapters(false)}
          chapters={manifest.chapters}
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
