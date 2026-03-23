import { Link, useLocalSearchParams } from "expo-router";
import { Text, View } from "react-native";

export default function BookDetailPage() {
  const { author, title } = useLocalSearchParams<{
    author: string;
    title: string;
  }>();

  return (
    <View className="flex-1 items-center justify-center bg-white px-6">
      <Text className="text-2xl font-bold mb-2">Book Detail</Text>
      <Text className="text-gray-500 mb-6">
        {author} / {title}
      </Text>
      <Link href="/" className="text-blue-600 underline">
        Back to catalog
      </Link>
    </View>
  );
}
