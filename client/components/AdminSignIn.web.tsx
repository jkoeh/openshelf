import { useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { discoveryColors } from "../constants/discovery";
import { useTheme } from "../hooks/useTheme";
import { verifyAdminIdentity } from "../lib/api";

type GoogleId = {
	initialize: (options: {
		client_id: string;
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
		script.src = "https://accounts.google.com/gsi/client";
		script.async = true;
		script.onload = () => resolve();
		script.onerror = () => reject(new Error("Google sign-in could not load."));
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
	const buttonRef = useRef<View>(null);

	useEffect(() => {
		if (!open || token || !clientId) return;
		let current = true;
		loadGoogleScript()
			.then(() => {
				if (!current || !window.google?.accounts?.id || !buttonRef.current) return;
				const google = window.google.accounts.id;
				google.initialize({
					client_id: clientId,
					callback: async ({ credential }) => {
						if (!credential || !current) return;
						setBusy(true);
						setError("");
						try {
							await verifyAdminIdentity(credential);
							if (current) onToken(credential);
						} catch {
							if (current) setError("This Google account is not authorized for owner controls.");
						} finally {
							if (current) setBusy(false);
						}
					},
				});
				google.renderButton(buttonRef.current as unknown as HTMLElement, {
					theme: "outline",
					size: "large",
					text: "signin_with",
					width: 220,
				});
			})
			.catch(() => {
				if (current) setError("Google sign-in could not load. Please retry.");
			});
		return () => {
			current = false;
		};
	}, [open, token, onToken]);

	return (
		<View style={{ alignItems: "flex-end", flexShrink: 0 }}>
			<Pressable
				accessibilityRole="button"
				accessibilityLabel="Owner controls"
				onPress={() => setOpen(!open)}
				style={{ minHeight: 44, justifyContent: "center", paddingHorizontal: 8 }}
			>
				<Text style={{ color: palette.primary, fontSize: 14, fontWeight: "600" }}>
					{token ? "Owner ✓" : "Owner controls"}
				</Text>
			</Pressable>
			{open && (
				<View
					style={{
						position: "absolute",
						top: 48,
						right: 0,
						zIndex: 20,
						width: 270,
						padding: 16,
						borderWidth: 1,
						borderColor: palette.border,
						borderRadius: 12,
						backgroundColor: palette.card,
					}}
				>
					<Text style={{ color: palette.text, fontWeight: "700", marginBottom: 8 }}>
						Owner controls
					</Text>
					{token ? (
						<>
							<Text style={{ color: palette.muted, lineHeight: 20 }}>
								Signed in as johnkoeh@gmail.com. Manage jobs from search results.
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
								<Text style={{ color: palette.primary, fontWeight: "600" }}>Sign out</Text>
							</Pressable>
						</>
					) : clientId ? (
						<>
							<Text style={{ color: palette.muted, lineHeight: 20, marginBottom: 12 }}>
								Sign in with the owner Google account to cancel, retry, or regenerate.
							</Text>
							<View ref={buttonRef} accessibilityLabel="Google sign-in button" />
							{busy && (
								<Text style={{ color: palette.muted, marginTop: 8 }}>Checking account…</Text>
							)}
						</>
					) : (
						<Text style={{ color: palette.muted, lineHeight: 20 }}>
							Owner sign-in needs a Google client ID in the site build.
						</Text>
					)}
					{!!error && (
						<Text accessibilityRole="alert" style={{ color: palette.text, marginTop: 8 }}>
							{error}
						</Text>
					)}
				</View>
			)}
		</View>
	);
}
