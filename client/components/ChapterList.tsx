import { useRouter } from "expo-router";
import { ChevronRight } from "lucide-react-native";
import { Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { formatDuration, sectionDisplayTitle } from "../lib/format";
import type { ManifestSection } from "../types";

interface SectionListProps {
	sections: ManifestSection[];
	author: string;
	title: string;
	rendition?: string;
	build?: string;
}

export default function ChapterList({
	sections,
	author,
	title,
	rendition,
	build,
}: SectionListProps) {
	const { colors } = useTheme();
	const router = useRouter();
	const sectionUrl = (sequence: number) => {
		const search = new URLSearchParams({ section: String(sequence), autoplay: "1" });
		if (rendition) search.set("rendition", rendition);
		if (build) search.set("build", build);
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
				Sections
			</Text>
			{sections.map((section) => (
				<Pressable
					key={section.sequence}
					onPress={() => router.push(sectionUrl(section.sequence))}
					style={({ pressed }) => ({
						flexDirection: "row",
						justifyContent: "space-between",
						alignItems: "center",
						paddingVertical: 13,
						paddingHorizontal: 20,
						backgroundColor: pressed ? colors.surface : "transparent",
					})}
				>
					<Text
						style={{ color: colors.text, fontSize: 17, letterSpacing: -0.2, flex: 1 }}
						numberOfLines={1}
					>
						{sectionDisplayTitle(section)}
					</Text>
					<View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
						<Text style={{ color: colors.textSecondary, fontSize: 14 }}>
							{formatDuration(section.duration_seconds)}
						</Text>
						<ChevronRight size={16} color={colors.textSecondary} strokeWidth={2.4} />
					</View>
				</Pressable>
			))}
		</View>
	);
}
