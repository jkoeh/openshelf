/**
 * Apple Books-inspired color palette.
 * Clean neutrals, SF-style system blue accent, subtle separators.
 */
export const themes = {
	light: {
		background: "#FFFFFF",
		surface: "#F2F2F7",
		text: "#000000",
		textSecondary: "#8E8E93",
		highlight: "#FFD54F",
		chunkTint: "rgba(255, 213, 79, 0.08)",
		card: "#FFFFFF",
		border: "rgba(60, 60, 67, 0.12)",
		primary: "#007AFF",
		primaryText: "#FFFFFF",
		playerBg: "#F9F9F9",
		separator: "rgba(60, 60, 67, 0.18)",
	},
	sepia: {
		background: "#F8F1E3",
		surface: "#EFE6D5",
		text: "#3B2F20",
		textSecondary: "#7D6E5B",
		highlight: "#D4A849",
		chunkTint: "rgba(212, 168, 73, 0.10)",
		card: "#F3ECE0",
		border: "rgba(59, 47, 32, 0.12)",
		primary: "#A0722B",
		primaryText: "#FFFFFF",
		playerBg: "#F0E9D9",
		separator: "rgba(59, 47, 32, 0.15)",
	},
	dark: {
		background: "#000000",
		surface: "#1C1C1E",
		text: "#FFFFFF",
		textSecondary: "#8E8E93",
		highlight: "#FFD54F",
		chunkTint: "rgba(255, 213, 79, 0.08)",
		card: "#1C1C1E",
		border: "rgba(84, 84, 88, 0.36)",
		primary: "#0A84FF",
		primaryText: "#FFFFFF",
		playerBg: "#1C1C1E",
		separator: "rgba(84, 84, 88, 0.36)",
	},
} as const;

export type ThemeName = keyof typeof themes;
export type ThemeColors = (typeof themes)[ThemeName];
