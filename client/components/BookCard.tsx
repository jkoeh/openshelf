import { Link } from "expo-router";
import { ChevronRight } from "lucide-react-native";
import { Image, Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { coverUrl } from "../lib/api";
import { formatDuration, stringToHue } from "../lib/format";
import type { CatalogBook } from "../types";

function CoverImage({ book }: { book: CatalogBook }) {
	const hue = stringToHue(book.title);

	if (book.has_cover) {
		return (
			<Image
				source={{ uri: coverUrl(book.author_slug, book.title_slug) }}
				style={{
					width: 60,
					height: 84,
					borderRadius: 6,
					backgroundColor: `hsl(${hue}, 20%, 85%)`,
				}}
				resizeMode="cover"
			/>
		);
	}

	// Fallback: generated color block
	return (
		<View
			style={{
				width: 60,
				height: 84,
				borderRadius: 6,
				backgroundColor: `hsl(${hue}, 40%, 55%)`,
				justifyContent: "flex-end",
				padding: 6,
			}}
		>
			<Text
				style={{ color: "#FFFFFF", fontSize: 10, fontWeight: "700", lineHeight: 12 }}
				numberOfLines={3}
			>
				{book.title}
			</Text>
		</View>
	);
}

export default function BookCard({ book }: { book: CatalogBook }) {
	const { colors } = useTheme();
	const search = new URLSearchParams({
		rendition: book.rendition,
		build: book.current_build,
	});

	return (
		<Link href={`/book/${book.author_slug}/${book.title_slug}?${search}`} asChild>
			<Pressable
				style={{
					flexDirection: "row",
					alignItems: "center",
					paddingVertical: 12,
					paddingHorizontal: 16,
					borderBottomWidth: 0.5,
					borderBottomColor: colors.separator,
				}}
			>
				{/* Cover */}
				<View
					style={{
						marginRight: 14,
						shadowColor: "#000",
						shadowOffset: { width: 0, height: 1 },
						shadowOpacity: 0.15,
						shadowRadius: 3,
						elevation: 2,
					}}
				>
					<CoverImage book={book} />
				</View>

				{/* Book info */}
				<View style={{ flex: 1 }}>
					<Text
						style={{
							color: colors.text,
							fontSize: 17,
							fontWeight: "600",
							marginBottom: 2,
							letterSpacing: -0.2,
						}}
						numberOfLines={2}
					>
						{book.title}
					</Text>
					<Text
						style={{ color: colors.textSecondary, fontSize: 15, marginBottom: 4 }}
						numberOfLines={1}
					>
						{book.author}
					</Text>
					<Text style={{ color: colors.textSecondary, fontSize: 13 }}>
						{book.section_count} sections · {formatDuration(book.total_duration_seconds)}
					</Text>
				</View>

				<ChevronRight size={18} color={colors.textSecondary} strokeWidth={2.4} />
			</Pressable>
		</Link>
	);
}
