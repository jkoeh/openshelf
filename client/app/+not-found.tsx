import { Link } from "expo-router";
import { Text, View } from "react-native";

export default function NotFoundPage() {
  return (
    <View className="flex-1 items-center justify-center bg-white">
      <Text className="text-2xl font-bold mb-2">Page not found</Text>
      <Link href="/" className="text-blue-600 underline">
        Back to catalog
      </Link>
    </View>
  );
}
