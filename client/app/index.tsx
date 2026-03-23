import { Link } from "expo-router";
import { Text, View } from "react-native";

export default function CatalogPage() {
  return (
    <View className="flex-1 items-center justify-center bg-white">
      <Text className="text-3xl font-bold mb-2">OpenShelf</Text>
      <Text className="text-gray-500 mb-8">
        Public domain audiobooks with text sync
      </Text>
      <Link href="/about" className="text-blue-600 underline">
        About
      </Link>
    </View>
  );
}
