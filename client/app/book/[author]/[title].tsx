import { Ionicons } from "@expo/vector-icons";
import { Link, useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, ScrollView, Text, View } from "react-native";
import ChapterList from "../../../components/ChapterList";
import Header from "../../../components/Header";
import { useTheme } from "../../../hooks/useTheme";
import { coverUrl, epubUrl, fetchBook } from "../../../lib/api";
import { formatDuration, stringToHue } from "../../../lib/format";
import { getSavedProgress } from "../../../lib/storage";
import type { Manifest } from "../../../types";

export default function BookDetailPage() {
	const { author, title } = useLocalSearchParams<{
		author: string;
		title: string;
	}>();
	const { colors } = useTheme();
	const router = useRouter();
	const [manifest, setManifest] = useState<Manifest | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!author || !title) return;
		setLoading(true);
		fetchBook(author, title)
			.then(setManifest)
			.catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
			.finally(() => setLoading(false));
	}, [author, title]);

	if (loading) {
		return (
			<View style={{ flex: 1, backgroundColor: colors.background }}>
				<Header showBack />
				<ActivityIndicator style={{ flex: 1 }} color={colors.primary} />
			</View>
		);
	}

	if (error || !manifest || !author || !title) {
		return (
			<View style={{ flex: 1, backgroundColor: colors.background }}>
				<Header title="Error" showBack />
				<View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
					<Text style={{ color: colors.textSecondary, fontSize: 17 }}>
						{error ?? "Book not found"}
					</Text>
				</View>
			</View>
		);
	}

	const progress = getSavedProgress(author, title);
	const hue = stringToHue(manifest.title);

	return (
		<View style={{ flex: 1, backgroundColor: colors.background }}>
			<Header showBack />
			<ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
				{/* Hero — book cover + metadata */}
				<View style={{ alignItems: "center", paddingTop: 24, paddingBottom: 20, paddingHorizontal: 24 }}>
					{/* Generated cover */}
					<View
						style={{
							width: 140,
							height: 200,
							borderRadius: 8,
							marginBottom: 20,
							shadowColor: "#000",
							shadowOffset: { width: 0, height: 4 },
							shadowOpacity: 0.25,
							shadowRadius: 10,
							elevation: 6,
							backgroundColor: `hsl(${hue}, 38%, 50%)`,
							overflow: "hidden",
						}}
					>
						<Image
							source={{ uri: coverUrl(author, title) }}
							style={{ width: 140, height: 200 }}
							resizeMode="cover"
						/>
					</View>

					<Text
						style={{
							color: colors.text,
							fontSize: 22,
							fontWeight: "700",
							textAlign: "center",
							letterSpacing: -0.3,
							marginBottom: 4,
						}}
					>
						{manifest.title}
					</Text>
					<Text
						style={{
							color: colors.primary,
							fontSize: 17,
							textAlign: "center",
							marginBottom: 8,
						}}
					>
						{manifest.author}
					</Text>
					<Text style={{ color: colors.textSecondary, fontSize: 14 }}>
						{formatDuration(manifest.total_duration_seconds)} · {manifest.chapters.length} chapters
					</Text>
				</View>

				{/* Action buttons */}
				<View style={{ paddingHorizontal: 20, gap: 10, marginBottom: 24 }}>
					{progress ? (
						<Pressable
							onPress={() =>
								router.push(
									`/read/${author}/${title}?chapter=${progress.chapter}&time=${progress.audioTime}`,
								)
							}
							style={{
								backgroundColor: colors.primary,
								borderRadius: 12,
								paddingVertical: 16,
								flexDirection: "row",
								alignItems: "center",
								justifyContent: "center",
								gap: 8,
							}}
						>
							<Ionicons name="play" size={18} color={colors.primaryText} />
							<Text style={{ color: colors.primaryText, fontSize: 17, fontWeight: "600" }}>
								Continue Chapter {progress.chapter}
							</Text>
						</Pressable>
					) : null}
					<Pressable
						onPress={() => router.push(`/read/${author}/${title}?autoplay=1`)}
						style={{
							backgroundColor: progress ? colors.surface : colors.primary,
							borderRadius: 12,
							paddingVertical: 16,
							flexDirection: "row",
							alignItems: "center",
							justifyContent: "center",
							gap: 8,
						}}
					>
						<Ionicons
							name="headset"
							size={18}
							color={progress ? colors.text : colors.primaryText}
						/>
						<Text
							style={{
								color: progress ? colors.text : colors.primaryText,
								fontSize: 17,
								fontWeight: "600",
							}}
						>
							Start Listening
						</Text>
					</Pressable>
					<View style={{ flexDirection: "row", gap: 10 }}>
						<Pressable
							onPress={() => router.push(`/read/${author}/${title}`)}
							style={{
								flex: 1,
								backgroundColor: colors.surface,
								borderRadius: 12,
								paddingVertical: 14,
								flexDirection: "row",
								alignItems: "center",
								justifyContent: "center",
								gap: 6,
							}}
						>
							<Ionicons name="book-outline" size={17} color={colors.text} />
							<Text style={{ color: colors.text, fontSize: 15, fontWeight: "500" }}>Read</Text>
						</Pressable>
						<Link href={epubUrl(author, title)} asChild>
							<Pressable
								style={{
									flex: 1,
									backgroundColor: colors.surface,
									borderRadius: 12,
									paddingVertical: 14,
									flexDirection: "row",
									alignItems: "center",
									justifyContent: "center",
									gap: 6,
								}}
							>
								<Ionicons name="download-outline" size={17} color={colors.text} />
								<Text style={{ color: colors.text, fontSize: 15, fontWeight: "500" }}>
									EPUB
								</Text>
							</Pressable>
						</Link>
					</View>
				</View>

				{/* Chapters */}
				<ChapterList chapters={manifest.chapters} author={author} title={title} />
			</ScrollView>
		</View>
	);
}
