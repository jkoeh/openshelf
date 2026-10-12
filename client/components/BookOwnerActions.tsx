import { useEffect, useState } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";
import { useOwner } from "../hooks/useOwner";
import { useTheme } from "../hooks/useTheme";
import {
	ApiError,
	cancelGenerationJob,
	deleteBook,
	fetchBookManagement,
	fetchGenerationJob,
	regenerateGenerationJob,
	requestExpressiveGenerationJob,
} from "../lib/api";
import type { BookManagement, GenerationJob } from "../types";

export default function BookOwnerActions({
	author,
	title,
	bookTitle,
	onDeleted,
	onRefresh,
}: {
	author: string;
	title: string;
	bookTitle: string;
	onDeleted: () => void;
	onRefresh: () => void;
}) {
	const { token, setToken } = useOwner();
	const { colors, theme } = useTheme();
	const [details, setDetails] = useState<BookManagement | null>(null);
	const [job, setJob] = useState<GenerationJob | null>(null);
	const [loading, setLoading] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const [pending, setPending] = useState<"delete" | "expressive" | null>(null);
	const [attempt, setAttempt] = useState(0);

	// biome-ignore lint/correctness/useExhaustiveDependencies: attempt explicitly triggers an owner-request retry.
	useEffect(() => {
		let alive = true;
		setDetails(null);
		setJob(null);
		setPending(null);
		if (!token) return;
		setLoading(true);
		setError("");
		fetchBookManagement(author, title, token)
			.then(async (data) => {
				if (!alive) return;
				setDetails(data);
				if (data.active_job_id) {
					const active = await fetchGenerationJob(data.active_job_id);
					if (alive) setJob(active);
				}
			})
			.catch((reason) => {
				if (!alive) return;
				if (reason instanceof ApiError && reason.status === 401) setToken(null);
				setError(
					reason instanceof Error
						? reason.message
						: "Could not load owner actions.",
				);
			})
			.finally(() => {
				if (alive) setLoading(false);
			});
		return () => {
			alive = false;
		};
	}, [author, title, token, attempt, setToken]);

	const active = job
		? job.state === "queued" || job.state === "running"
		: !!details?.active_job_id;
	const jobId = job?.id;
	useEffect(() => {
		if (!token || !active || !jobId) return;
		let alive = true;
		const refresh = () =>
			fetchGenerationJob(jobId)
				.then((updated) => {
					if (alive) {
						setJob(updated);
						setError("");
					}
				})
				.catch(() => {
					if (alive)
						setError(
							"Could not refresh generation status. It will retry shortly.",
						);
				});
		refresh();
		const timer = setInterval(refresh, 15_000);
		return () => {
			alive = false;
			clearInterval(timer);
		};
	}, [token, active, jobId]);

	async function act(action: "standard" | "expressive" | "cancel" | "delete") {
		if (!token || busy || !details) return;
		setBusy(true);
		setError("");
		try {
			if (action === "delete") {
				await deleteBook(author, title, token);
				onDeleted();
			} else if (action === "cancel" && job) {
				setJob(await cancelGenerationJob(job.id, token));
			} else if (details.source_id) {
				setJob(
					action === "expressive"
						? await requestExpressiveGenerationJob(
								details.source_id,
								token,
								true,
							)
						: await regenerateGenerationJob(details.source_id, token),
				);
			}
			setPending(null);
		} catch (reason) {
			if (reason instanceof ApiError && reason.status === 401) setToken(null);
			if (reason instanceof ApiError && reason.code === "DELETE_INCOMPLETE")
				setDetails((previous) =>
					previous ? { ...previous, deleting: true } : previous,
				);
			setError(
				reason instanceof Error
					? reason.message
					: "Owner action failed. Try again.",
			);
		} finally {
			setBusy(false);
		}
	}

	if (!token)
		return error ? (
			<Text
				accessibilityRole="alert"
				style={{ color: colors.textSecondary, padding: 16 }}
			>
				{error} Sign in again using Owner controls.
			</Text>
		) : null;
	const disabled = busy || loading;
	const button = (
		label: string,
		onPress: () => void,
		unavailable = false,
		destructive = false,
	) => (
		<Pressable
			accessibilityRole="button"
			accessibilityLabel={label}
			accessibilityState={{ disabled: disabled || unavailable }}
			disabled={disabled || unavailable}
			onPress={onPress}
			style={{
				padding: 12,
				minHeight: 44,
				borderRadius: 8,
				borderWidth: 1,
				borderColor: colors.border,
				opacity: disabled || unavailable ? 0.5 : 1,
			}}
		>
			<Text
				style={{
					color: destructive
						? theme === "dark"
							? "#ff8a80"
							: "#b42318"
						: colors.primary,
					fontWeight: "600",
				}}
			>
				{label}
			</Text>
		</Pressable>
	);
	return (
		<View
			style={{
				margin: 16,
				padding: 16,
				borderRadius: 12,
				backgroundColor: colors.surface,
				gap: 12,
			}}
		>
			<Text
				accessibilityRole="header"
				style={{ color: colors.text, fontSize: 18, fontWeight: "700" }}
			>
				Manage book
			</Text>
			{loading || busy ? (
				<ActivityIndicator
					accessibilityLabel={
						busy ? "Applying owner action" : "Loading owner actions"
					}
					color={colors.primary}
				/>
			) : null}
			{error ? (
				<Text accessibilityRole="alert" style={{ color: colors.text }}>
					{error}
				</Text>
			) : null}
			{!details && !loading
				? button("Retry owner actions", () => setAttempt((value) => value + 1))
				: null}
			{details?.active_job_id && !job && error
				? button("Retry generation status", () =>
						setAttempt((value) => value + 1),
					)
				: null}
			{details ? (
				<>
					<Text style={{ color: colors.textSecondary }}>
						{details.deleting
							? "Deletion needs cleanup. Retry Delete book to finish."
							: details.source_id
								? "Regenerate audio using the current pipeline. The existing audiobook stays playable until the new audio is ready."
								: "This book has no linked indexed source. Regeneration is unavailable; you can still delete it."}
					</Text>
					{job ? (
						<Text
							accessibilityLiveRegion="polite"
							style={{ color: colors.text }}
						>
							{job.state === "completed"
								? "New audio is ready. Refresh builds to select it."
								: job.state === "failed"
									? "Regeneration failed. You can start a fresh generation."
									: job.state === "canceled"
										? "Regeneration canceled."
										: `Regeneration ${job.state} · ${job.stage}`}
						</Text>
					) : null}
					{job?.state === "completed"
						? button("Refresh builds", onRefresh)
						: null}
					{active && job
						? button("Cancel regeneration", () => act("cancel"))
						: null}
					{pending ? (
						<View style={{ gap: 12 }}>
							<Text style={{ color: colors.text, fontWeight: "600" }}>
								{pending === "delete"
									? `Delete “${bookTitle}”? This permanently removes all published audio renditions and builds, the cover, and the published EPUB. Source reading remains available.`
									: "Expressive regeneration uses paid OpenAI direction on your PC and Chatterbox narration. Each accepted request uses one of 300 daily starts."}
							</Text>
							<View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
								{button(
									pending === "delete"
										? "Confirm delete book"
										: "Confirm expressive regeneration",
									() => act(pending === "delete" ? "delete" : "expressive"),
									active,
									pending === "delete",
								)}
								{button(pending === "delete" ? "Keep book" : "Cancel", () =>
									setPending(null),
								)}
							</View>
						</View>
					) : (
						<View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
							{button(
								"Regenerate audio",
								() => act("standard"),
								active || !details.source_id || details.deleting,
							)}
							{button(
								"Regenerate expressive audio",
								() => setPending("expressive"),
								active || !details.source_id || details.deleting,
							)}
							{button("Delete book", () => setPending("delete"), active, true)}
						</View>
					)}
					{active ? (
						<Text style={{ color: colors.textSecondary }}>
							Cancel or finish generation before deleting this book.
						</Text>
					) : null}
				</>
			) : null}
		</View>
	);
}
