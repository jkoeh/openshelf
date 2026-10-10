import { useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
import {
	AccessibilityInfo,
	ActivityIndicator,
	Animated,
	Pressable,
	ScrollView,
	Text,
	View,
} from "react-native";
import Header from "../../components/Header";
import { useTheme } from "../../hooks/useTheme";
import {
	createGenerationJob,
	fetchGenerationJob,
	sourceEpubUrl,
} from "../../lib/api";
import { audioCreation } from "../../lib/audio-creation";
import { parseSourceEpub, type SourceSection } from "../../lib/source-epub";
import type { GenerationJob } from "../../types";

export default function SourceReader() {
	const params = useLocalSearchParams<{
		id: string;
		title: string;
		author: string;
		job?: string;
		state?: string;
	}>();
	const router = useRouter();
	const { colors } = useTheme();
	const [sections, setSections] = useState<SourceSection[]>([]);
	const [section, setSection] = useState(0);
	const [job, setJob] = useState<GenerationJob | null>(null);
	const [audioError, setAudioError] = useState("");
	const [textError, setTextError] = useState("");
	const [attempt, setAttempt] = useState(0);
	const scroll = useRef<ScrollView>(null);
	const bob = useRef(new Animated.Value(0)).current;
	const started = useRef<{
		id: string;
		promise: Promise<GenerationJob>;
	} | null>(null);
	const progress = audioCreation(job);
	const active =
		!audioError && (!job || job.state === "queued" || job.state === "running");

	useEffect(() => {
		let alive = true;
		let animation: Animated.CompositeAnimation | undefined;
		const update = (reduced: boolean) => {
			animation?.stop();
			bob.setValue(0);
			if (active && !reduced && alive) {
				animation = Animated.loop(
					Animated.sequence([
						Animated.timing(bob, {
							toValue: -5,
							duration: 700,
							useNativeDriver: true,
						}),
						Animated.timing(bob, {
							toValue: 0,
							duration: 700,
							useNativeDriver: true,
						}),
					]),
				);
				animation.start();
			}
		};
		AccessibilityInfo.isReduceMotionEnabled().then(update);
		const subscription = AccessibilityInfo.addEventListener(
			"reduceMotionChanged",
			update,
		);
		return () => {
			alive = false;
			animation?.stop();
			subscription.remove();
		};
	}, [active, bob]);

	useEffect(() => {
		const controller = new AbortController();
		setTextError("");
		setSections([]);
		fetch(`${sourceEpubUrl(params.id)}?inline=1`, { signal: controller.signal })
			.then(async (response) => {
				if (!response.ok)
					throw new Error("Could not load the book. Try again shortly.");
				const bytes = new Uint8Array(await response.arrayBuffer());
				const content = parseSourceEpub(bytes);
				if (!controller.signal.aborted) {
					setSections(content);
					setSection(0);
				}
			})
			.catch((error) => {
				if (!controller.signal.aborted) setTextError(error.message);
			});
		return () => controller.abort();
	}, [params.id, attempt]);

	useEffect(() => {
		let alive = true;
		let timer: ReturnType<typeof setTimeout> | undefined;
		let currentId = params.job;
		const follow = async (promise: Promise<GenerationJob>) => {
			try {
				const next = await promise;
				if (!alive) return;
				currentId = next.id;
				setJob(next);
				setAudioError("");
				if (next.state === "queued" || next.state === "running") {
					timer = setTimeout(() => follow(fetchGenerationJob(next.id)), 15_000);
				}
			} catch (error) {
				if (!alive) return;
				setAudioError(
					error instanceof Error
						? error.message
						: "Audio is unavailable. You can keep reading.",
				);
				// Retry status reads without submitting a duplicate generation job.
				if (currentId)
					timer = setTimeout(
						() => follow(fetchGenerationJob(currentId!)),
						15_000,
					);
			}
		};
		if (params.job) follow(fetchGenerationJob(params.job));
		else if (params.state === "failed" || params.state === "canceled")
			setAudioError("Audio is unavailable. You can keep reading.");
		else {
			if (started.current?.id !== params.id)
				started.current = {
					id: params.id,
					promise: createGenerationJob(params.id),
				};
			follow(started.current.promise);
		}
		return () => {
			alive = false;
			if (timer) clearTimeout(timer);
		};
	}, [params.id, params.job, params.state]);

	const ready = job?.state === "completed" && job.author_slug && job.title_slug;
	return (
		<View style={{ flex: 1, backgroundColor: colors.background }}>
			<Header showBack title={params.title || "Read now"} />
			<View
				style={{
					padding: 16,
					borderBottomWidth: 1,
					borderColor: colors.separator,
				}}
			>
				{ready ? (
					<View>
						<Text
							accessibilityLiveRegion="polite"
							style={{
								color: colors.text,
								fontWeight: "600",
								marginBottom: 12,
							}}
						>
							🦉 Ready to soar · 100%
						</Text>
						<Pressable
							accessibilityRole="button"
							onPress={() =>
								router.push({
									pathname: "/read/[author]/[title]",
									params: {
										author: job.author_slug!,
										title: job.title_slug!,
										autoplay: "1",
										rendition:
											job.mode === "expressive"
												? "chatterbox-af-heart"
												: "kokoro-af-heart",
									},
								})
							}
							style={{
								backgroundColor: colors.primary,
								borderRadius: 12,
								padding: 16,
								alignItems: "center",
							}}
						>
							<Text style={{ color: "white", fontWeight: "600", fontSize: 17 }}>
								Start Listening
							</Text>
						</Pressable>
					</View>
				) : (
					<View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
						<Animated.Text
							accessible={false}
							style={{ fontSize: 36, transform: [{ translateY: bob }] }}
						>
							🦉
						</Animated.Text>
						<View style={{ flex: 1 }} accessibilityLiveRegion="polite">
							<Text
								style={{ color: colors.text, fontSize: 17, fontWeight: "600" }}
							>
								{audioError ? "Taking a breather" : progress.verb}
								{!audioError && active ? ` · ${progress.percent}%` : ""}
							</Text>
							<Text
								style={{
									color: colors.textSecondary,
									lineHeight: 21,
									marginTop: 4,
								}}
							>
								{audioError || progress.detail}
							</Text>
							{active && (
								<View
									accessibilityRole="progressbar"
									accessibilityValue={{
										min: 0,
										max: 100,
										now: progress.percent,
									}}
									style={{
										height: 4,
										backgroundColor: colors.separator,
										borderRadius: 4,
										marginTop: 10,
									}}
								>
									<View
										style={{
											width: `${progress.percent}%`,
											height: 4,
											backgroundColor: colors.primary,
										}}
									/>
								</View>
							)}
						</View>
					</View>
				)}
			</View>
			{textError ? (
				<View style={{ padding: 24 }}>
					<Text accessibilityRole="alert" style={{ color: colors.text }}>
						{textError}
					</Text>
					<Pressable
						accessibilityRole="button"
						onPress={() => setAttempt((value) => value + 1)}
						style={{ paddingVertical: 16 }}
					>
						<Text style={{ color: colors.primary }}>Retry loading text</Text>
					</Pressable>
				</View>
			) : !sections.length ? (
				<ActivityIndicator
					accessibilityLabel="Opening book text"
					style={{ marginTop: 32 }}
					color={colors.primary}
				/>
			) : (
				<ScrollView
					ref={scroll}
					contentContainerStyle={{
						padding: 24,
						maxWidth: 760,
						width: "100%",
						alignSelf: "center",
					}}
				>
					<Text style={{ color: colors.textSecondary, marginBottom: 20 }}>
						{params.author} · Section {section + 1} of {sections.length}
					</Text>
					{sections[section].paragraphs.map((paragraph, index) => (
						<Text
							key={index}
							selectable
							style={{
								color: colors.text,
								fontFamily: "Georgia",
								fontSize: index === 0 ? 25 : 19,
								fontWeight: index === 0 ? "600" : "400",
								lineHeight: 31,
								marginBottom: 18,
							}}
						>
							{paragraph}
						</Text>
					))}
					<View
						style={{
							flexDirection: "row",
							justifyContent: "space-between",
							marginVertical: 16,
						}}
					>
						{[
							["Previous section", -1],
							["Next section", 1],
						].map(([label, step]) => (
							<Pressable
								key={label}
								accessibilityRole="button"
								disabled={
									section + Number(step) < 0 ||
									section + Number(step) >= sections.length
								}
								onPress={() => {
									setSection((value) => value + Number(step));
									scroll.current?.scrollTo({ y: 0, animated: false });
								}}
								style={{
									padding: 12,
									opacity:
										section + Number(step) < 0 ||
										section + Number(step) >= sections.length
											? 0.35
											: 1,
								}}
							>
								<Text style={{ color: colors.primary }}>{label}</Text>
							</Pressable>
						))}
					</View>
				</ScrollView>
			)}
		</View>
	);
}
