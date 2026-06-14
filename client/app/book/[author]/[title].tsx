import { Link, useLocalSearchParams, useRouter } from "expo-router";
import {
	BookOpen,
	ChevronDown,
	ChevronUp,
	Download,
	Headphones,
	Play,
} from "lucide-react-native";
import { useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, ScrollView, Text, View } from "react-native";
import ChapterList from "../../../components/ChapterList";
import Header from "../../../components/Header";
import { useTheme } from "../../../hooks/useTheme";
import { coverUrl, epubUrl, fetchBook, fetchBookBuilds } from "../../../lib/api";
import { formatDuration, stringToHue } from "../../../lib/format";
import { selectRendition } from "../../../lib/renditions";
import {
	getSavedBuildSelection,
	getSavedProgress,
	saveBuildSelection,
} from "../../../lib/storage";
import type { BookBuildOption, BookBuildsResponse, Manifest } from "../../../types";

function formatUploadedAt(value?: string) {
	if (!value) return "Upload time unavailable";
	const uploaded = new Date(value);
	if (Number.isNaN(uploaded.getTime())) return "Upload time unavailable";
	return `${uploaded.toLocaleDateString(undefined, {
		month: "short",
		day: "numeric",
		year: "numeric",
	})} ${uploaded.toLocaleTimeString(undefined, {
		hour: "numeric",
		minute: "2-digit",
	})}`;
}

export default function BookDetailPage() {
	const { author, title, rendition, build } = useLocalSearchParams<{
		author: string;
		title: string;
		rendition?: string;
		build?: string;
	}>();
	const { colors } = useTheme();
	const router = useRouter();
	const [manifest, setManifest] = useState<Manifest | null>(null);
	const [builds, setBuilds] = useState<BookBuildsResponse | null>(null);
	const [selectedRenditionKey, setSelectedRenditionKey] = useState<string | null>(null);
	const [selectedBuildId, setSelectedBuildId] = useState<string | null>(null);
	const [selectedEngine, setSelectedEngine] = useState<string | null>(null);
	const [renditionSelectorOpen, setRenditionSelectorOpen] = useState(false);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!author || !title) return;
		let cancelled = false;
		setLoading(true);
		setError(null);
		setManifest(null);
		setBuilds(null);
		const saved = getSavedBuildSelection(author, title);
		setSelectedRenditionKey(saved?.rendition ?? rendition ?? null);
		setSelectedBuildId(saved?.build ?? build ?? null);
		setSelectedEngine(null);
		setRenditionSelectorOpen(false);
		fetchBook(author, title)
			.then((data) => {
				if (!cancelled) setManifest(data);
			})
			.catch((e) => {
				if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
			})
			.finally(() => {
				if (!cancelled) setLoading(false);
			});
		fetchBookBuilds(author, title)
			.then((data) => {
				if (!cancelled) setBuilds(data);
			})
			.catch(() => {
				if (!cancelled) setBuilds(null);
			});
		return () => {
			cancelled = true;
		};
	}, [author, title, rendition, build]);

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

	const hue = stringToHue(manifest.title);
	const selected = selectRendition(manifest, selectedRenditionKey ?? undefined);

	if (!selected) {
		return (
			<View style={{ flex: 1, backgroundColor: colors.background }}>
				<Header title="Error" showBack />
				<View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
					<Text style={{ color: colors.textSecondary, fontSize: 17 }}>
						No playable rendition found
					</Text>
				</View>
			</View>
		);
	}

	const buildOptions = builds?.renditions[selected.key]?.builds ?? [];
	const selectedBuild = selectedBuildId
		? (buildOptions.find((option) => option.build === selectedBuildId) ?? null)
		: null;
	const activeBuild =
		selectedBuild ?? buildOptions.find((option) => option.is_current) ?? null;
	const activeBuildId = activeBuild?.build ?? selected.rendition.current_build;
	const activeDuration =
		activeBuild?.total_duration_seconds ?? selected.rendition.total_duration_seconds;
	const activeChapters = activeBuild?.chapters ?? selected.rendition.chapters;
	const progress = getSavedProgress(author, title, selected.key, activeBuildId);
	const selectionGroups = builds
		? Object.entries(builds.renditions).reduce<
				{
					engine: string;
					voices: {
						rendition: string;
						display: string;
						voice: string;
						builds: BookBuildOption[];
					}[];
				}[]
			>((groups, [renditionKey, rendition]) => {
				let engineGroup = groups.find((group) => group.engine === rendition.engine);
				if (!engineGroup) {
					engineGroup = { engine: rendition.engine, voices: [] };
					groups.push(engineGroup);
				}
				engineGroup.voices.push({
					rendition: renditionKey,
					display: rendition.display,
					voice: rendition.voice,
					builds: rendition.builds,
				});
				return groups;
			}, [])
		: [];
	const activeEngine =
		selectedEngine ??
		activeBuild?.engine ??
		selected.rendition.engine ??
		selectionGroups[0]?.engine ??
		null;
	const visibleEngineGroup =
		selectionGroups.find((group) => group.engine === activeEngine) ?? selectionGroups[0];
	const selectedBuildRendition = builds?.renditions[selected.key] ?? null;
	const selectedDisplay = selectedBuildRendition?.display ?? selected.rendition.display;
	const selectedVoice =
		activeBuild?.voice ?? selectedBuildRendition?.voice ?? selected.rendition.voice;
	const selectedEngineLabel =
		activeBuild?.engine ?? selectedBuildRendition?.engine ?? selected.rendition.engine;
	const selectedUploadLabel = activeBuild?.uploaded_at
		? formatUploadedAt(activeBuild.uploaded_at)
		: null;

	const handleBuildSelect = (renditionKey: string, option: BookBuildOption) => {
		setSelectedRenditionKey(renditionKey);
		setSelectedBuildId(option.build);
		setSelectedEngine(option.engine);
		setRenditionSelectorOpen(false);
		saveBuildSelection(author, title, {
			rendition: renditionKey,
			build: option.build,
			updatedAt: new Date().toISOString(),
		});
	};

	const readUrl = (params: Record<string, string | number> = {}) => {
		const search = new URLSearchParams({ rendition: selected.key });
		search.set("build", activeBuildId);
		for (const [key, value] of Object.entries(params)) {
			search.set(key, String(value));
		}
		return `/read/${author}/${title}?${search}`;
	};

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
						{formatDuration(activeDuration)} · {activeChapters.length} chapters
					</Text>
					{visibleEngineGroup ? (
						<View
							style={{
								width: "100%",
								marginTop: 18,
								borderRadius: 12,
								backgroundColor: colors.surface,
								overflow: "hidden",
							}}
						>
							<Pressable
								onPress={() => {
									setSelectedEngine(selectedEngineLabel);
									setRenditionSelectorOpen((open) => !open);
								}}
								style={{
									padding: 14,
									flexDirection: "row",
									alignItems: "center",
									justifyContent: "space-between",
									gap: 12,
								}}
							>
								<View style={{ flex: 1 }}>
									<Text
										style={{
											color: colors.textSecondary,
											fontSize: 13,
											fontWeight: "600",
											textTransform: "uppercase",
											letterSpacing: 0.5,
											marginBottom: 6,
										}}
									>
										Rendition
									</Text>
									<Text
										style={{ color: colors.text, fontSize: 16, fontWeight: "700" }}
										numberOfLines={1}
									>
										{selectedDisplay}
									</Text>
									<Text
										style={{ color: colors.textSecondary, fontSize: 13, marginTop: 3 }}
										numberOfLines={1}
									>
										{selectedEngineLabel} - {selectedVoice}
									</Text>
									<Text
										style={{ color: colors.textSecondary, fontSize: 12, marginTop: 3 }}
										numberOfLines={1}
									>
										{selectedUploadLabel
											? `Uploaded ${selectedUploadLabel}`
											: "Backend default build"}
									</Text>
								</View>
								{renditionSelectorOpen ? (
									<ChevronUp size={20} color={colors.textSecondary} strokeWidth={2.2} />
								) : (
									<ChevronDown size={20} color={colors.textSecondary} strokeWidth={2.2} />
								)}
							</Pressable>
							{renditionSelectorOpen ? (
								<View style={{ paddingHorizontal: 14, paddingBottom: 14 }}>
									<View
										style={{
											height: 1,
											backgroundColor: colors.border,
											marginBottom: 12,
										}}
									/>
									<Text
										style={{
											color: colors.textSecondary,
											fontSize: 12,
											fontWeight: "700",
											textTransform: "uppercase",
											letterSpacing: 0.5,
											marginBottom: 8,
										}}
									>
										Engine
									</Text>
									{selectionGroups.length > 1 ? (
										<View
											style={{
												flexDirection: "row",
												flexWrap: "wrap",
												gap: 8,
												marginBottom: 12,
											}}
										>
											{selectionGroups.map((group) => {
												const active = group.engine === visibleEngineGroup.engine;
												return (
													<Pressable
														key={group.engine}
														onPress={() => setSelectedEngine(group.engine)}
														style={{
															minWidth: 96,
															borderRadius: 9,
															paddingVertical: 9,
															paddingHorizontal: 12,
															backgroundColor: active ? colors.primary : colors.card,
															alignItems: "center",
														}}
													>
														<Text
															style={{
																color: active ? colors.primaryText : colors.text,
																fontSize: 14,
																fontWeight: "700",
															}}
															numberOfLines={1}
														>
															{group.engine}
														</Text>
													</Pressable>
												);
											})}
										</View>
									) : (
										<View
											style={{
												borderWidth: 1,
												borderColor: colors.border,
												borderRadius: 9,
												paddingVertical: 9,
												paddingHorizontal: 12,
												backgroundColor: colors.card,
												marginBottom: 12,
											}}
										>
											<Text
												style={{ color: colors.text, fontSize: 14, fontWeight: "700" }}
												numberOfLines={1}
											>
												{visibleEngineGroup.engine}
											</Text>
										</View>
									)}
									{visibleEngineGroup.voices.map((voice) => (
										<View key={voice.rendition} style={{ marginTop: 12 }}>
											<View style={{ marginBottom: 8 }}>
												<Text
													style={{ color: colors.text, fontSize: 15, fontWeight: "700" }}
													numberOfLines={1}
												>
													{voice.display}
												</Text>
												<Text
													style={{ color: colors.textSecondary, fontSize: 12, marginTop: 2 }}
													numberOfLines={1}
												>
													{voice.voice}
												</Text>
											</View>
											{voice.builds.length > 0 ? (
												<View
													style={{
														borderWidth: 1,
														borderColor: colors.border,
														borderRadius: 10,
														overflow: "hidden",
													}}
												>
													{voice.builds.map((option, index) => {
														const active =
															voice.rendition === selected.key &&
															option.build === activeBuildId;
														return (
															<Pressable
																key={`${voice.rendition}:${option.build}`}
																onPress={() => handleBuildSelect(voice.rendition, option)}
																style={{
																	backgroundColor: active ? colors.primary : colors.card,
																	borderTopWidth: index === 0 ? 0 : 1,
																	borderTopColor: colors.border,
																	flexDirection: "row",
																	alignItems: "center",
																	justifyContent: "space-between",
																	gap: 10,
																	paddingVertical: 12,
																	paddingHorizontal: 12,
																}}
															>
																<View style={{ flex: 1 }}>
																	<Text
																		style={{
																			color: active ? colors.primaryText : colors.text,
																			fontSize: 14,
																			fontWeight: "700",
																		}}
																		numberOfLines={1}
																	>
																		{formatUploadedAt(option.uploaded_at)}
																	</Text>
																	<Text
																		style={{
																			color: active
																				? colors.primaryText
																				: colors.textSecondary,
																			fontSize: 12,
																			marginTop: 3,
																		}}
																		numberOfLines={1}
																	>
																		{formatDuration(option.total_duration_seconds)} -{" "}
																		{option.chapter_count}{" "}
																		{option.chapter_count === 1 ? "chapter" : "chapters"}
																		{option.is_current ? " - Backend default" : ""}
																	</Text>
																</View>
																{active ? (
																	<Text
																		style={{
																			color: colors.primaryText,
																			fontSize: 12,
																			fontWeight: "700",
																		}}
																	>
																		Selected
																	</Text>
																) : null}
															</Pressable>
														);
													})}
												</View>
											) : (
												<Text style={{ color: colors.textSecondary, fontSize: 13 }}>
													No retained uploads
												</Text>
											)}
										</View>
									))}
								</View>
							) : null}
						</View>
					) : null}
				</View>

				{/* Action buttons */}
				<View style={{ paddingHorizontal: 20, gap: 10, marginBottom: 24 }}>
					{progress ? (
						<Pressable
							onPress={() =>
								router.push(
									readUrl({ chapter: progress.chapter, time: progress.audioTime }),
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
							<Play size={18} color={colors.primaryText} strokeWidth={2.4} />
							<Text style={{ color: colors.primaryText, fontSize: 17, fontWeight: "600" }}>
								Continue Chapter {progress.chapter}
							</Text>
						</Pressable>
					) : null}
					<Pressable
						onPress={() => router.push(readUrl({ autoplay: 1 }))}
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
						<Headphones
							size={18}
							color={progress ? colors.text : colors.primaryText}
							strokeWidth={2.4}
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
							onPress={() => router.push(readUrl())}
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
							<BookOpen size={17} color={colors.text} strokeWidth={2.3} />
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
								<Download size={17} color={colors.text} strokeWidth={2.3} />
								<Text style={{ color: colors.text, fontSize: 15, fontWeight: "500" }}>
									EPUB
								</Text>
							</Pressable>
						</Link>
					</View>
				</View>

				{/* Chapters */}
				<ChapterList
					chapters={activeChapters}
					author={author}
					title={title}
					rendition={selected.key}
					build={activeBuildId}
				/>
			</ScrollView>
		</View>
	);
}
