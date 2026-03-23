import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { type ThemeColors, type ThemeName, themes } from "../constants/colors";
import { getSavedTheme, saveTheme } from "../lib/storage";

interface ThemeContextValue {
	theme: ThemeName;
	colors: ThemeColors;
	setTheme: (theme: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function getInitialTheme(): ThemeName {
	const saved = getSavedTheme();
	if (saved === "light" || saved === "sepia" || saved === "dark") return saved;
	return "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
	const [theme, setThemeState] = useState<ThemeName>(getInitialTheme);

	const setTheme = useCallback((t: ThemeName) => {
		setThemeState(t);
		saveTheme(t);
	}, []);

	const value = useMemo(
		() => ({ theme, colors: themes[theme], setTheme }),
		[theme, setTheme],
	);

	return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
	const ctx = useContext(ThemeContext);
	if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
	return ctx;
}
