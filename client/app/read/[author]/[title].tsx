import { useLocalSearchParams } from "expo-router";
import { Text, View } from "react-native";
import Header from "../../../components/Header";
import { useTheme } from "../../../hooks/useTheme";

export default function ReaderPage() {
  const { author, title } = useLocalSearchParams<{
    author: string;
    title: string;
  }>();
  const { colors } = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title={title ?? "Reader"} showBack />
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
        <Text style={{ color: colors.textSecondary, fontSize: 16, marginBottom: 8 }}>
          {author} / {title}
        </Text>
        <Text style={{ color: colors.textSecondary, fontSize: 14 }}>
          Reader will appear here
        </Text>
      </View>
    </View>
  );
}
