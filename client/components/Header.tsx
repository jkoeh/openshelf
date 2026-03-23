import { Link, useRouter } from "expo-router";
import { Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useTheme } from "../hooks/useTheme";

interface HeaderProps {
  title?: string;
  showBack?: boolean;
  rightElement?: React.ReactNode;
}

export default function Header({
  title = "OpenShelf",
  showBack = false,
  rightElement,
}: HeaderProps) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  return (
    <View
      style={{
        paddingTop: insets.top,
        backgroundColor: colors.background,
        borderBottomWidth: 1,
        borderBottomColor: colors.border,
      }}
    >
      <View
        style={{
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "space-between",
          paddingHorizontal: 16,
          height: 48,
        }}
      >
        <View style={{ flexDirection: "row", alignItems: "center", flex: 1 }}>
          {showBack ? (
            <Pressable onPress={() => router.back()} style={{ marginRight: 12 }}>
              <Text style={{ color: colors.primary, fontSize: 16 }}>{"← Back"}</Text>
            </Pressable>
          ) : null}
          <Text
            style={{ color: colors.text, fontSize: 18, fontWeight: "700" }}
            numberOfLines={1}
          >
            {title}
          </Text>
        </View>
        {rightElement ?? (
          <Link href="/about">
            <Text style={{ color: colors.primary, fontSize: 14 }}>About</Text>
          </Link>
        )}
      </View>
    </View>
  );
}
