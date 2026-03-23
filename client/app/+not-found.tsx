import { Link } from "expo-router";
import { Text, View } from "react-native";
import Header from "../components/Header";
import { useTheme } from "../hooks/useTheme";

export default function NotFoundPage() {
  const { colors } = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header title="Not Found" showBack />
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
        <Text style={{ color: colors.text, fontSize: 20, fontWeight: "700", marginBottom: 12 }}>
          Page not found
        </Text>
        <Link href="/">
          <Text style={{ color: colors.primary }}>Back to catalog</Text>
        </Link>
      </View>
    </View>
  );
}
