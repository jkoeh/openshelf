import { useRouter } from "expo-router";
import { Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { formatDuration } from "../lib/format";
import type { ManifestChapter } from "../types";

interface ChapterListProps {
  chapters: ManifestChapter[];
  author: string;
  title: string;
}

export default function ChapterList({ chapters, author, title }: ChapterListProps) {
  const { colors } = useTheme();
  const router = useRouter();

  return (
    <View>
      <Text
        style={{
          color: colors.text,
          fontSize: 16,
          fontWeight: "700",
          marginBottom: 12,
          paddingHorizontal: 16,
        }}
      >
        Chapters
      </Text>
      {chapters.map((ch) => (
        <Pressable
          key={ch.number}
          onPress={() =>
            router.push(`/read/${author}/${title}?chapter=${ch.number}`)
          }
          style={({ pressed }) => ({
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
            paddingVertical: 14,
            paddingHorizontal: 16,
            backgroundColor: pressed ? colors.chunkTint : "transparent",
            borderBottomWidth: 1,
            borderBottomColor: colors.border,
          })}
        >
          <View style={{ flex: 1, marginRight: 12 }}>
            <Text style={{ color: colors.text, fontSize: 15 }} numberOfLines={1}>
              {ch.number}. {ch.title}
            </Text>
          </View>
          <Text style={{ color: colors.textSecondary, fontSize: 13 }}>
            {formatDuration(ch.duration_seconds)}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}
