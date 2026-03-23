import { Link, useLocalSearchParams } from "expo-router";
import { Text, View } from "react-native";
import Header from "../../../components/Header";
import { useTheme } from "../../../hooks/useTheme";

export default function BookDetailPage() {
  const { author, title } = useLocalSearchParams<{
    author: string;
    title: string;
  }>();
  const { colors } = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title={title ?? "Book"} showBack />
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
        <Text style={{ color: colors.textSecondary, fontSize: 16, marginBottom: 8 }}>
          {author} / {title}
        </Text>
        <Text style={{ color: colors.textSecondary, fontSize: 14, marginBottom: 24 }}>
          Book detail will appear here
        </Text>
        <Link href={`/read/${author}/${title}`}>
          <Text style={{ color: colors.primary, fontSize: 14 }}>
            Open Reader →
          </Text>
        </Link>
      </View>
    </View>
  );
}
