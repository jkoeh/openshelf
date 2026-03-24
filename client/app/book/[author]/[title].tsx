import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  View,
} from "react-native";
import ChapterList from "../../../components/ChapterList";
import Header from "../../../components/Header";
import { useTheme } from "../../../hooks/useTheme";
import { epubUrl, fetchBook } from "../../../lib/api";
import { formatDuration, stringToHue } from "../../../lib/format";
import { getSavedProgress } from "../../../lib/storage";
import type { Manifest } from "../../../types";

export default function BookDetailPage() {
  const { author, title } = useLocalSearchParams<{
    author: string;
    title: string;
  }>();
  const { colors } = useTheme();
  const router = useRouter();
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!author || !title) return;
    setLoading(true);
    fetchBook(author, title)
      .then(setManifest)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [author, title]);

  if (loading) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Loading..." showBack />
        <ActivityIndicator style={{ flex: 1 }} color={colors.primary} />
      </View>
    );
  }

  if (error || !manifest || !author || !title) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.background }}>
        <Header title="Error" showBack />
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <Text style={{ color: colors.textSecondary }}>{error ?? "Book not found"}</Text>
        </View>
      </View>
    );
  }

  const progress = getSavedProgress(author, title);
  const hue = stringToHue(manifest.title);

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title={manifest.title} showBack />
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
        {/* Hero */}
        <View
          style={{
            backgroundColor: `hsl(${hue}, 50%, 92%)`,
            paddingVertical: 32,
            paddingHorizontal: 16,
            alignItems: "center",
          }}
        >
          <Text
            style={{
              color: colors.text,
              fontSize: 24,
              fontWeight: "700",
              textAlign: "center",
              marginBottom: 8,
            }}
          >
            {manifest.title}
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 16, marginBottom: 12 }}>
            {manifest.author}
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 14 }}>
            {formatDuration(manifest.total_duration_seconds)} ·{" "}
            {manifest.chapters.length} chapters · {manifest.source}
          </Text>
        </View>

        {/* Actions */}
        <View style={{ padding: 16, gap: 10 }}>
          {progress ? (
            <Pressable
              onPress={() =>
                router.push(
                  `/read/${author}/${title}?chapter=${progress.chapter}&time=${progress.audioTime}`,
                )
              }
              style={{
                backgroundColor: colors.primary,
                borderRadius: 8,
                paddingVertical: 14,
                alignItems: "center",
              }}
            >
              <Text style={{ color: "#FFFFFF", fontSize: 16, fontWeight: "600" }}>
                Continue from Chapter {progress.chapter}
              </Text>
            </Pressable>
          ) : null}
          <Pressable
            onPress={() => router.push(`/read/${author}/${title}?autoplay=1`)}
            style={{
              backgroundColor: progress ? colors.card : colors.primary,
              borderRadius: 8,
              paddingVertical: 14,
              alignItems: "center",
              borderWidth: progress ? 1 : 0,
              borderColor: colors.border,
            }}
          >
            <Text
              style={{
                color: progress ? colors.text : "#FFFFFF",
                fontSize: 16,
                fontWeight: "600",
              }}
            >
              Start Listening
            </Text>
          </Pressable>
          <Pressable
            onPress={() => router.push(`/read/${author}/${title}`)}
            style={{
              backgroundColor: colors.card,
              borderRadius: 8,
              paddingVertical: 14,
              alignItems: "center",
              borderWidth: 1,
              borderColor: colors.border,
            }}
          >
            <Text style={{ color: colors.text, fontSize: 16 }}>Start Reading</Text>
          </Pressable>
          <Link href={epubUrl(author, title)} asChild>
            <Pressable
              style={{
                backgroundColor: colors.card,
                borderRadius: 8,
                paddingVertical: 14,
                alignItems: "center",
                borderWidth: 1,
                borderColor: colors.border,
              }}
            >
              <Text style={{ color: colors.text, fontSize: 16 }}>Download EPUB</Text>
            </Pressable>
          </Link>
        </View>

        {/* Chapters */}
        <ChapterList chapters={manifest.chapters} author={author} title={title} />
      </ScrollView>
    </View>
  );
}
