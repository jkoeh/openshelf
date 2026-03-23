import { Link } from "expo-router";
import { Text, View } from "react-native";
import Header from "../components/Header";
import { useTheme } from "../hooks/useTheme";

export default function CatalogPage() {
  const { colors } = useTheme();

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header />
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
        <Text style={{ color: colors.textSecondary, fontSize: 16, marginBottom: 16 }}>
          Catalog will appear here
        </Text>
        <Link href="/book/franz-kafka/the-trial">
          <Text style={{ color: colors.primary, fontSize: 14 }}>
            Demo: The Trial →
          </Text>
        </Link>
      </View>
    </View>
  );
}
