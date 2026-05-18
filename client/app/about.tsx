import { Link } from "expo-router";
import { ScrollView, Text, View } from "react-native";
import Header from "../components/Header";
import { useTheme } from "../hooks/useTheme";

export default function AboutPage() {
  const { colors } = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title="About" showBack />
      <ScrollView
        contentContainerStyle={{
          padding: 24,
          maxWidth: 600,
          width: "100%",
          alignSelf: "center",
        }}
      >
        <Text
          style={{
            color: colors.text,
            fontSize: 28,
            fontWeight: "700",
            marginBottom: 16,
          }}
        >
          OpenShelf
        </Text>

        <Text style={{ color: colors.text, fontSize: 16, lineHeight: 26, marginBottom: 20 }}>
          OpenShelf is an open source public domain audiobook platform. It downloads EPUB books
          from Project Gutenberg and Standard Ebooks, converts them to AI-narrated audio using
          Kokoro TTS with word-level timestamps, and serves them globally via
          Cloudflare R2.
        </Text>

        <Text
          style={{
            color: colors.text,
            fontSize: 18,
            fontWeight: "600",
            marginBottom: 8,
          }}
        >
          Features
        </Text>
        <Text style={{ color: colors.textSecondary, fontSize: 15, lineHeight: 24, marginBottom: 20 }}>
          {"\u2022"} Word-level text highlighting synced to narration{"\n"}
          {"\u2022"} Tap any word to seek audio to that point{"\n"}
          {"\u2022"} Three reading themes: Light, Sepia, and Dark{"\n"}
          {"\u2022"} Adjustable font size and playback speed{"\n"}
          {"\u2022"} Background audio with lock screen controls{"\n"}
          {"\u2022"} Reading progress saved automatically{"\n"}
          {"\u2022"} EPUB downloads for offline reading
        </Text>

        <Text
          style={{
            color: colors.text,
            fontSize: 18,
            fontWeight: "600",
            marginBottom: 8,
          }}
        >
          Keyboard Shortcuts (Web)
        </Text>
        <Text style={{ color: colors.textSecondary, fontSize: 15, lineHeight: 24, marginBottom: 20 }}>
          {"\u2022"} Space — Play / Pause{"\n"}
          {"\u2022"} Left Arrow — Seek back 10s{"\n"}
          {"\u2022"} Right Arrow — Seek forward 10s
        </Text>

        <Text
          style={{
            color: colors.text,
            fontSize: 18,
            fontWeight: "600",
            marginBottom: 8,
          }}
        >
          Open Source
        </Text>
        <Text style={{ color: colors.textSecondary, fontSize: 15, lineHeight: 24, marginBottom: 20 }}>
          All books on OpenShelf are in the public domain. The platform itself is open source —
          contributions are welcome.
        </Text>

        <Link href="/">
          <Text style={{ color: colors.primary, fontSize: 16 }}>Browse Books</Text>
        </Link>
      </ScrollView>
    </View>
  );
}
