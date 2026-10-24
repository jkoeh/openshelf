import { Link } from "expo-router";
import { createElement, useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Platform, Pressable, Text, TextInput, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import {
	type OwnerUpload,
	cancelGenerationJob,
	createGenerationJob,
	fetchGenerationJob,
	fetchOwnerUploads,
	retryGenerationJob,
	uploadOwnerDocument,
} from "../lib/api";
import type { GenerationJob } from "../types";

export default function OwnerDocuments({
	token,
	onExpired,
}: { token: string; onExpired: () => void }) {
	const { colors } = useTheme();
	const [open, setOpen] = useState(false);
	const [title, setTitle] = useState("");
	const [author, setAuthor] = useState("Owner");
	const [text, setText] = useState("");
	const [language, setLanguage] = useState<"en" | "zh">("en");
	const [file, setFile] = useState<File | null>(null);
	const [ack, setAck] = useState(false);
	const [uploads, setUploads] = useState<OwnerUpload[]>([]);
	const [selected, setSelected] = useState<OwnerUpload | null>(null);
	const [job, setJob] = useState<GenerationJob | null>(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const fail = useCallback(
		(e: unknown) => {
			const value = e as { status?: number; message?: string };
			if (value.status === 401) onExpired();
			setError(value.message ?? "Document request failed");
		},
		[onExpired],
	);
	useEffect(() => {
		if (open)
			fetchOwnerUploads(token)
				.then((result) => setUploads(result.uploads))
				.catch(fail);
	}, [open, token, fail]);
	useEffect(() => {
		if (!job || !["queued", "running"].includes(job.state)) return;
		const timer = setInterval(
			() => fetchGenerationJob(job.id, token).then(setJob).catch(fail),
			5000,
		);
		return () => clearInterval(timer);
	}, [job, token, fail]);
	const start = async (upload: OwnerUpload) => {
		setSelected(upload);
		setJob(await createGenerationJob(upload.source_id, token));
	};
	const submit = async () => {
		setBusy(true);
		setError(null);
		try {
			if (!ack) throw new Error("Confirm distribution rights and public publication first.");
			if (!title.trim() || !author.trim()) throw new Error("Enter a title and author.");
			const format = file ? file.name.split(".").pop()?.toLowerCase() : "txt";
			if (format !== "pdf" && format !== "docx" && format !== "txt")
				throw new Error("Choose PDF, DOCX or TXT. Legacy .doc files are unsupported.");
			const bytes = file ?? new Blob([text], { type: "text/plain;charset=utf-8" });
			if (!bytes.size || bytes.size > 16 * 1024 * 1024)
				throw new Error("Provide nonempty text or a file of at most 16 MiB.");
			const upload = await uploadOwnerDocument(
				token,
				{ title: title.trim(), author: author.trim(), language, format },
				bytes,
			);
			setUploads((previous) => [upload, ...previous]);
			setSelected(upload);
			await start(upload);
		} catch (e) {
			fail(e);
		} finally {
			setBusy(false);
		}
	};
	const action = async (run: () => Promise<GenerationJob>) => {
		setBusy(true);
		setError(null);
		try {
			setJob(await run());
		} catch (e) {
			fail(e);
		} finally {
			setBusy(false);
		}
	};
	const button = (label: string, press: () => void, disabled = false) => (
		<Pressable
			accessibilityRole="button"
			accessibilityLabel={label}
			disabled={disabled}
			onPress={press}
			style={{ paddingVertical: 10, opacity: disabled ? 0.5 : 1 }}
		>
			<Text style={{ color: colors.primary }}>{label}</Text>
		</Pressable>
	);
	const inputStyle = {
		color: colors.text,
		borderWidth: 1,
		borderColor: colors.border,
		borderRadius: 8,
		padding: 10,
		marginTop: 8,
	};
	return (
		<View style={{ marginTop: 20 }}>
			{button(open ? "Close owner documents" : "Upload or paste text to audio", () =>
				setOpen((value) => !value),
			)}
			{open && (
				<View
					style={{
						padding: 14,
						borderWidth: 1,
						borderColor: colors.border,
						borderRadius: 10,
						gap: 8,
					}}
				>
					<Text accessibilityRole="header" style={{ color: colors.text, fontSize: 20 }}>
						Owner documents
					</Text>
					<Text style={{ color: colors.text }}>
						Text PDFs, Word .docx, UTF-8 .txt or pasted text. Maximum 16 MiB. Scanned/encrypted PDFs
						and legacy .doc files are unsupported.
					</Text>
					<TextInput
						accessibilityLabel="Document title"
						placeholder="Title"
						value={title}
						onChangeText={setTitle}
						style={inputStyle}
					/>
					<TextInput
						accessibilityLabel="Document author"
						placeholder="Author"
						value={author}
						onChangeText={setAuthor}
						style={inputStyle}
					/>
					<View style={{ flexDirection: "row", gap: 18 }}>
						{button(`${language === "en" ? "Selected: " : ""}English`, () => setLanguage("en"))}
						{button(`${language === "zh" ? "Selected: " : ""}Mandarin Chinese`, () =>
							setLanguage("zh"),
						)}
					</View>
					{Platform.OS === "web" &&
						createElement("input", {
							type: "file",
							accept: ".pdf,.docx,.txt",
							"aria-label": "Choose document file",
							onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
								setFile(event.target.files?.[0] ?? null);
								setText("");
							},
							style: { color: colors.text, maxWidth: "100%" },
						})}
					{Platform.OS !== "web" && (
						<Text style={{ color: colors.text }}>
							File uploads are available in the web client. You can paste text here.
						</Text>
					)}
					{file ? (
						<View>
							<Text style={{ color: colors.text }}>Selected: {file.name}</Text>
							{button("Use pasted text instead", () => setFile(null))}
						</View>
					) : (
						<TextInput
							accessibilityLabel="Paste document text"
							placeholder="Paste your text here"
							multiline
							value={text}
							onChangeText={setText}
							style={[inputStyle, { minHeight: 140, textAlignVertical: "top" }]}
						/>
					)}
					<Pressable
						accessibilityRole="checkbox"
						accessibilityLabel="I have distribution rights and agree to public audio and EPUB publication"
						accessibilityState={{ checked: ack }}
						onPress={() => setAck((value) => !value)}
					>
						<Text style={{ color: colors.text }}>
							{ack ? "☑" : "☐"} I have distribution rights and agree that the finished audiobook and
							annotated EPUB will be public. The original upload stays private.
						</Text>
					</Pressable>
					{button(
						"Upload and generate audio",
						() => {
							void submit();
						},
						busy || !ack,
					)}
					{busy && <ActivityIndicator />}
					{error && (
						<Text accessibilityRole="alert" style={{ color: colors.text }}>
							{error}
						</Text>
					)}
					{selected && <Text style={{ color: colors.text }}>Document: {selected.title}</Text>}
					{job && (
						<View>
							<Text accessibilityLiveRegion="polite" style={{ color: colors.text }}>
								Audio: {job.state} · {job.stage}
								{job.error_code ? ` · ${job.error_code}` : ""}
							</Text>
							{job.error_code === "INVALID_DOCUMENT" && (
								<Text style={{ color: colors.text }}>
									The document could not be extracted. Use a text PDF, unencrypted DOCX or UTF-8
									TXT. Scanned PDFs require OCR before upload.
								</Text>
							)}
							{["queued", "running"].includes(job.state) &&
								button(
									"Cancel document audio",
									() => {
										void action(() => cancelGenerationJob(job.id, token));
									},
									busy,
								)}
							{["failed", "canceled"].includes(job.state) &&
								button(
									"Retry document audio",
									() => {
										void action(() => retryGenerationJob(job.id, token));
									},
									busy,
								)}
							{job.state === "completed" && job.author_slug && job.title_slug && (
								<Link
									accessibilityLabel="Listen to document"
									href={`/book/${job.author_slug}/${job.title_slug}`}
								>
									Listen to document
								</Link>
							)}
						</View>
					)}
					{uploads.length > 0 && (
						<Text style={{ color: colors.text, fontWeight: "700" }}>Recent uploads</Text>
					)}
					{uploads.slice(0, 10).map((upload) => (
						<View key={upload.id}>
							<Text style={{ color: colors.text }}>
								{upload.title} · {upload.language} · {upload.format}
							</Text>
							{upload.author_slug && upload.title_slug ? (
								<Link href={`/book/${upload.author_slug}/${upload.title_slug}`}>
									Listen to {upload.title}
								</Link>
							) : (
								button(
									`${upload.job_id ? "Follow" : "Generate"} ${upload.title}`,
									() => {
										void action(async () => {
											setSelected(upload);
											return upload.job_id
												? fetchGenerationJob(upload.job_id, token)
												: createGenerationJob(upload.source_id, token);
										});
									},
									busy,
								)
							)}
						</View>
					))}
				</View>
			)}
		</View>
	);
}
