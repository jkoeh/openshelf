import { Text, View } from "react-native";
import Header from "../components/Header";
import { useTheme } from "../hooks/useTheme";

export default function AboutPage() {
  const { colors } = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title="About" showBack />
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
        <Text
          style={{
            color: colors.text,
            fontSize: 16,
            textAlign: "center",
            maxWidth: 500,
            lineHeight: 24,
          }}
        >
          OpenShelf is an open source public domain audiobook platform. It
          downloads EPUB books from Project Gutenberg and Standard Ebooks,
          converts them to AI-narrated audio with word-level text/audio sync,
          and serves them globally via Cloudflare R2.
        </Text>
      </View>
    </View>
  );
}
