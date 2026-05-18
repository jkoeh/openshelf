import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { formatDuration } from "../lib/format";
import type { ManifestChapter } from "../types";

interface ChapterListProps {
	chapters: ManifestChapter[];
	author: string;
	title: string;
	rendition?: string;
}

export default function ChapterList({ chapters, author, title, rendition }: ChapterListProps) {
	const { colors } = useTheme();
	const router = useRouter();
	const chapterUrl = (chapter: number) => {
		const search = new URLSearchParams({ chapter: String(chapter), autoplay: "1" });
		if (rendition) search.set("rendition", rendition);
		return `/read/${author}/${title}?${search}`;
	};

	return (
		<View>
			<Text
				style={{
					color: colors.text,
					fontSize: 22,
					fontWeight: "700",
					letterSpacing: -0.3,
					paddingHorizontal: 20,
					paddingBottom: 8,
				}}
			>
				Chapters
			</Text>
			{chapters.map((ch) => (
				<Pressable
					key={ch.number}
					onPress={() => router.push(chapterUrl(ch.number))}
					style={({ pressed }) => ({
						flexDirection: "row",
						justifyContent: "space-between",
						alignItems: "center",
						paddingVertical: 13,
						paddingHorizontal: 20,
						backgroundColor: pressed ? colors.surface : "transparent",
					})}
				>
					<View style={{ flex: 1, marginRight: 12 }}>
						<Text
							style={{ color: colors.text, fontSize: 17, letterSpacing: -0.2 }}
							numberOfLines={1}
						>
							{ch.number}. {ch.title}
						</Text>
					</View>
					<View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
						<Text
							style={{
								color: colors.textSecondary,
								fontSize: 14,
								fontVariant: ["tabular-nums"],
							}}
						>
							{formatDuration(ch.duration_seconds)}
						</Text>
						<Ionicons name="chevron-forward" size={16} color={colors.textSecondary} />
					</View>
				</Pressable>
			))}
		</View>
	);
}
