import { X } from "lucide-react-native";
import { useEffect, useRef, useState } from "react";
import {
	ActivityIndicator,
	Modal,
	Pressable,
	ScrollView,
	Text,
	View,
} from "react-native";
import { discoveryColors } from "../constants/discovery";
import { useTheme } from "../hooks/useTheme";
import { ApiError, verifyAdminIdentity } from "../lib/api";

type GoogleId = {
	initialize: (options: {
		client_id: string;
		auto_select: boolean;
		callback: (response: { credential?: string }) => void;
	}) => void;
	renderButton: (
		element: HTMLElement,
		options: { theme: string; size: string; text: string; width: number },
	) => void;
	disableAutoSelect: () => void;
};
declare global {
	interface Window {
		google?: { accounts?: { id?: GoogleId } };
	}
}

const clientId = process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID;
let scriptLoading: Promise<void> | null = null;

function loadGoogleScript(): Promise<void> {
	if (window.google?.accounts?.id) return Promise.resolve();
	if (scriptLoading) return scriptLoading;
	const loading = new Promise<void>((resolve, reject) => {
		const script = document.createElement("script");
		const timer = window.setTimeout(() => {
			script.remove();
			reject(new Error("Google sign-in timed out."));
		}, 10_000);
		script.src = "https://accounts.google.com/gsi/client";
		script.async = true;
		script.onload = () => {
			window.clearTimeout(timer);
			if (window.google?.accounts?.id) resolve();
			else {
				script.remove();
				reject(new Error("Google sign-in is unavailable."));
			}
		};
		script.onerror = () => {
			window.clearTimeout(timer);
			script.remove();
			reject(new Error("Google sign-in could not load."));
		};
		document.head.appendChild(script);
	});
	scriptLoading = loading.catch((error) => {
		scriptLoading = null;
		throw error;
	});
	return scriptLoading;
}

export default function AdminSignIn({
	token,
	onToken,
}: {
	token: string | null;
	onToken: (token: string | null) => void;
}) {
	const { colors, theme } = useTheme();
	const palette = discoveryColors(theme, colors);
	const [open, setOpen] = useState(false);
	const [error, setError] = useState("");
	const [busy, setBusy] = useState(false);
	const [loading, setLoading] = useState(false);
	const [attempt, setAttempt] = useState(0);
	const [buttonHost, setButtonHost] = useState<View | null>(null);
	const triggerRef = useRef<View>(null);

	// biome-ignore lint/correctness/useExhaustiveDependencies: attempt explicitly restarts Google sign-in when Retry is pressed.
	useEffect(() => {
		if (!open || token || !clientId || !buttonHost) return;
		let current = true;
		setLoading(true);
		setBusy(false);
		setError("");
		loadGoogleScript()
			.then(() => {
				if (!current) return;
				const google = window.google?.accounts?.id;
				if (!google) throw new Error("Google sign-in is unavailable.");
				const element = buttonHost as unknown as HTMLElement;
				element.replaceChildren();
				google.initialize({
					client_id: clientId,
					auto_select: false,
					callback: async ({ credential }) => {
						if (!current) return;
						if (!credential) {
							setError(
								"Google did not return a sign-in credential. Please try again.",
							);
							return;
						}
						setBusy(true);
						setError("");
						try {
							await verifyAdminIdentity(credential);
							if (current) {
								onToken(credential);
								setOpen(false);
							}
						} catch (reason) {
							if (current) {
								setError(
									reason instanceof ApiError && reason.status === 401
										? "This Google account is not authorized. Please sign in with the owner account."
										: reason instanceof ApiError && reason.status === 503
											? "Owner sign-in is temporarily unavailable. Please try again later."
											: reason instanceof ApiError && reason.status === 429
												? "Too many sign-in attempts. Please wait a minute and try again."
												: "Could not check your account. Check your connection and try again.",
								);
							}
						} finally {
							if (current) setBusy(false);
						}
					},
				});
				google.renderButton(element, {
					theme: "outline",
					size: "large",
					text: "signin_with",
					width: Math.min(320, element.clientWidth),
				});
				setLoading(false);
			})
			.catch(() => {
				if (current) {
					setLoading(false);
					setError(
						"Google sign-in could not load. Check your connection and retry.",
					);
				}
			});
		return () => {
			current = false;
		};
	}, [open, token, onToken, buttonHost, attempt]);

	useEffect(() => {
		if (!open) return;
		const previous = document.body.style.overflow;
		document.body.style.overflow = "hidden";
		return () => {
			document.body.style.overflow = previous;
		};
	}, [open]);

	const close = () => setOpen(false);

	return (
		<View style={{ alignItems: "flex-end", flexShrink: 0 }}>
			<Pressable
				ref={triggerRef}
				accessibilityRole="button"
				accessibilityLabel="Owner controls"
				onPress={() => {
					setError("");
					setBusy(false);
					setOpen(true);
				}}
				style={{
					minHeight: 44,
					justifyContent: "center",
					paddingHorizontal: 8,
				}}
			>
				<Text
					style={{ color: palette.primary, fontSize: 14, fontWeight: "600" }}
				>
					{token ? "Owner ✓" : "Owner controls"}
				</Text>
			</Pressable>
			<Modal
				transparent
				visible={open}
				animationType="fade"
				onRequestClose={close}
				accessibilityLabel="Owner controls"
				onDismiss={() =>
					(triggerRef.current as unknown as HTMLElement | null)?.focus()
				}
			>
				<View
					style={{
						flex: 1,
						justifyContent: "center",
						alignItems: "center",
						padding: 20,
					}}
				>
					<Pressable
						accessible={false}
						onPress={close}
						testID="owner-modal-backdrop"
						style={{
							position: "absolute",
							top: 0,
							right: 0,
							bottom: 0,
							left: 0,
							backgroundColor: "rgba(16, 33, 62, 0.48)",
						}}
					/>
					<View
						testID="owner-modal-card"
						style={{
							width: "100%",
							maxWidth: 420,
							maxHeight: "100%",
							backgroundColor: palette.card,
							borderRadius: 20,
							borderWidth: 1,
							borderColor: palette.border,
						}}
					>
						<View
							style={{
								flexDirection: "row",
								alignItems: "center",
								justifyContent: "space-between",
								paddingLeft: 24,
								paddingRight: 12,
								paddingTop: 12,
							}}
						>
							<Text
								accessibilityRole="header"
								style={{ color: palette.text, fontSize: 22, fontWeight: "700" }}
							>
								Owner controls
							</Text>
							<Pressable
								accessibilityRole="button"
								accessibilityLabel="Close owner controls"
								onPress={close}
								style={{
									width: 44,
									height: 44,
									alignItems: "center",
									justifyContent: "center",
								}}
							>
								<X size={22} color={palette.muted} />
							</Pressable>
						</View>
						<ScrollView
							style={{ flexShrink: 1 }}
							contentContainerStyle={{ padding: 24, paddingTop: 12 }}
						>
							{token ? (
								<>
									<Text style={{ color: palette.muted, lineHeight: 20 }}>
										Signed in as johnkoeh@gmail.com. Manage jobs from search
										results.
									</Text>
									<Pressable
										accessibilityRole="button"
										onPress={() => {
											window.google?.accounts?.id?.disableAutoSelect();
											onToken(null);
											setOpen(false);
										}}
										style={{ minHeight: 44, justifyContent: "center" }}
									>
										<Text style={{ color: palette.primary, fontWeight: "600" }}>
											Sign out
										</Text>
									</Pressable>
								</>
							) : clientId ? (
								<>
									<Text
										style={{
											color: palette.muted,
											lineHeight: 20,
											marginBottom: 12,
										}}
									>
										Sign in with the owner Google account to manage jobs and
										request expressive audio.
									</Text>
									<View
										ref={setButtonHost}
										accessibilityLabel="Google sign-in button"
										style={{
											minHeight: 44,
											alignItems: "center",
											pointerEvents: busy ? "none" : "auto",
										}}
									/>
									{loading && (
										<View
											style={{
												flexDirection: "row",
												gap: 10,
												alignItems: "center",
												marginTop: 8,
											}}
										>
											<ActivityIndicator color={palette.primary} />
											<Text
												accessibilityLiveRegion="polite"
												style={{ color: palette.muted }}
											>
												Loading Google sign-in…
											</Text>
										</View>
									)}
									{busy && (
										<Text
											accessibilityLiveRegion="polite"
											style={{ color: palette.muted, marginTop: 8 }}
										>
											Checking account…
										</Text>
									)}
								</>
							) : (
								<>
									<Text
										style={{
											color: palette.text,
											fontSize: 16,
											fontWeight: "600",
											marginBottom: 8,
										}}
									>
										Owner sign-in is unavailable
									</Text>
									<Text style={{ color: palette.muted, lineHeight: 22 }}>
										Google sign-in has not been enabled for this site yet.
										Please try again once setup is complete.
									</Text>
									<Pressable
										accessibilityRole="button"
										onPress={close}
										style={{
											minHeight: 44,
											justifyContent: "center",
											marginTop: 12,
										}}
									>
										<Text style={{ color: palette.primary, fontWeight: "600" }}>
											Close
										</Text>
									</Pressable>
								</>
							)}
							{!!error && (
								<Text
									accessibilityRole="alert"
									style={{ color: palette.text, marginTop: 8 }}
								>
									{error}
								</Text>
							)}
							{!!error && clientId && !busy && (
								<Pressable
									accessibilityRole="button"
									onPress={() => setAttempt((value) => value + 1)}
									style={{
										minHeight: 44,
										justifyContent: "center",
										marginTop: 8,
									}}
								>
									<Text style={{ color: palette.primary, fontWeight: "600" }}>
										Retry sign-in
									</Text>
								</Pressable>
							)}
						</ScrollView>
					</View>
				</View>
			</Modal>
		</View>
	);
}
