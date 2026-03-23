import { Link } from "expo-router";
import { Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { formatDuration, stringToHue } from "../lib/format";
import type { CatalogBook } from "../types";

export default function BookCard({ book }: { book: CatalogBook }) {
  const { colors } = useTheme();
  const hue = stringToHue(book.title);

  return (
    <Link href={`/book/${book.author_slug}/${book.title_slug}`} asChild>
      <View
        style={{
          backgroundColor: colors.card,
          borderRadius: 12,
          borderWidth: 1,
          borderColor: colors.border,
          overflow: "hidden",
          marginBottom: 12,
        }}
      >
        <View
          style={{
            height: 8,
            backgroundColor: `hsl(${hue}, 60%, 65%)`,
          }}
        />
        <View style={{ padding: 16 }}>
          <Text
            style={{
              color: colors.text,
              fontSize: 18,
              fontWeight: "700",
              marginBottom: 4,
            }}
            numberOfLines={2}
          >
            {book.title}
          </Text>
          <Text
            style={{ color: colors.textSecondary, fontSize: 14, marginBottom: 8 }}
            numberOfLines={1}
          >
            {book.author}
          </Text>
          <Text style={{ color: colors.textSecondary, fontSize: 13 }}>
            {book.chapter_count} ch · {formatDuration(book.total_duration_seconds)}
          </Text>
        </View>
      </View>
    </Link>
  );
}
