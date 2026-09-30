import type { ThemeColors, ThemeName } from "./colors";

export function discoveryColors(theme: ThemeName, colors: ThemeColors) {
  return theme === "light"
    ? {
        background: "#FBFAF7",
        card: "#FFFFFF",
        text: "#10213E",
        muted: "#607087",
        border: "#DDE2E8",
        primary: "#2864C3",
        primaryText: "#FFFFFF",
      }
    : {
        background: colors.background,
        card: colors.card,
        text: colors.text,
        muted: colors.textSecondary,
        border: colors.border,
        primary: colors.primary,
        primaryText: colors.primaryText,
      };
}
