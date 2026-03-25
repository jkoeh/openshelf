import { useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, View } from "react-native";
import ChapterDropdown from "../../../components/ChapterDropdown";
import Header from "../../../components/Header";
import ReadingPane from "../../../components/ReadingPane";
import SettingsPanel from "../../../components/SettingsPanel";
import { useTheme } from "../../../hooks/useTheme";
import { fetchBook, fetchChapter } from "../../../lib/api";
import { getSavedFontSize, saveFontSize } from "../../../lib/storage";
import type { ChapterResponse, Manifest } from "../../../types";

export default function ReaderPage() {
  const { author, title, chapter: chapterParam } = useLocalSearchParams<{
    author: string;
    title: string;
    chapter?: string;
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
  const scrollRef = useRef<ScrollView>(null);

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
      setCurrentChapter(clamped);
    },
    [manifest],
  );

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
                  {currentChapter}/{manifest.chapters.length}
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

      {/* Chapter navigation footer */}
      {manifest && !loading ? (
        <View
          style={{
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
            paddingHorizontal: 16,
            paddingVertical: 12,
            borderTopWidth: 1,
            borderTopColor: colors.border,
            backgroundColor: colors.background,
          }}
        >
          <Pressable
            onPress={() => goToChapter(currentChapter - 1)}
            disabled={currentChapter <= 1}
            style={{ opacity: currentChapter <= 1 ? 0.3 : 1 }}
          >
            <Text style={{ color: colors.primary, fontSize: 14 }}>Previous</Text>
          </Pressable>
          <Text style={{ color: colors.textSecondary, fontSize: 13 }}>
            Chapter {currentChapter} of {manifest.chapters.length}
          </Text>
          <Pressable
            onPress={() => goToChapter(currentChapter + 1)}
            disabled={currentChapter >= manifest.chapters.length}
            style={{ opacity: currentChapter >= manifest.chapters.length ? 0.3 : 1 }}
          >
            <Text style={{ color: colors.primary, fontSize: 14 }}>Next</Text>
          </Pressable>
        </View>
      ) : null}

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
