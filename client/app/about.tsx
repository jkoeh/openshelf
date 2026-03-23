import { Link } from "expo-router";
import { Text, View } from "react-native";

export default function AboutPage() {
  return (
    <View className="flex-1 items-center justify-center bg-white px-6">
      <Text className="text-2xl font-bold mb-4">About OpenShelf</Text>
      <Text className="text-gray-600 text-center mb-6 max-w-lg">
        OpenShelf is an open source public domain audiobook platform. It
        downloads EPUB books from Project Gutenberg and Standard Ebooks,
        converts them to AI-narrated audio with word-level text/audio sync, and
        serves them globally via Cloudflare R2.
      </Text>
      <Link href="/" className="text-blue-600 underline">
        Back to catalog
      </Link>
    </View>
  );
}
