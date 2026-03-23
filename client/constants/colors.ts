export const themes = {
  light: {
    background: "#FFFFFF",
    text: "#1A1A1A",
    textSecondary: "#6B7280",
    highlight: "#FFD700",
    chunkTint: "#F5F5DC",
    card: "#F9FAFB",
    border: "#E5E7EB",
    primary: "#2563EB",
  },
  sepia: {
    background: "#F4ECD8",
    text: "#5B4636",
    textSecondary: "#8B7355",
    highlight: "#E8A317",
    chunkTint: "#EDE0C8",
    card: "#EDE0C8",
    border: "#D4C4A8",
    primary: "#B8860B",
  },
  dark: {
    background: "#1A1A2E",
    text: "#E0E0E0",
    textSecondary: "#9CA3AF",
    highlight: "#FFD700",
    chunkTint: "#2A2A3E",
    card: "#2A2A3E",
    border: "#3A3A4E",
    primary: "#60A5FA",
  },
} as const;

export type ThemeName = keyof typeof themes;
export type ThemeColors = (typeof themes)[ThemeName];
