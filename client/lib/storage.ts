import { createMMKV } from "react-native-mmkv";

let _storage: ReturnType<typeof createMMKV> | null = null;

function getStorage() {
	if (!_storage) _storage = createMMKV();
	return _storage;
}

const KEYS = {
	theme: "settings.theme",
	fontSize: "settings.fontSize",
	playbackRate: "settings.playbackRate",
	syncEnabled: "settings.syncEnabled",
} as const;

export function getSavedTheme(): string | undefined {
	return getStorage().getString(KEYS.theme);
}

export function saveTheme(theme: string): void {
	getStorage().set(KEYS.theme, theme);
}

export function getSavedFontSize(): number {
	return getStorage().getNumber(KEYS.fontSize) || 18;
}

export function saveFontSize(size: number): void {
	getStorage().set(KEYS.fontSize, size);
}

export function getSavedPlaybackRate(): number {
	return getStorage().getNumber(KEYS.playbackRate) || 1.0;
}

export function savePlaybackRate(rate: number): void {
	getStorage().set(KEYS.playbackRate, rate);
}

export function isSyncEnabled(): boolean {
	const val = getStorage().getString(KEYS.syncEnabled);
	return val === undefined ? true : val === "true";
}

export function saveSyncEnabled(enabled: boolean): void {
	getStorage().set(KEYS.syncEnabled, String(enabled));
}

export interface ReadingProgress {
	chapter: number;
	audioTime: number;
	updatedAt: string;
}

export interface SavedBuildSelection {
	rendition: string;
	build: string;
	updatedAt: string;
}

function progressKey(author: string, title: string, rendition: string, build: string): string {
	return `progress:${author}/${title}/${rendition}/${build}`;
}

function buildSelectionKey(author: string, title: string): string {
	return `build-selection:${author}/${title}`;
}

export function getSavedProgress(
	author: string,
	title: string,
	rendition: string,
	build: string,
): ReadingProgress | null {
	const raw = getStorage().getString(progressKey(author, title, rendition, build));
	if (!raw) return null;
	try {
		return JSON.parse(raw) as ReadingProgress;
	} catch {
		return null;
	}
}

export function saveProgress(
	author: string,
	title: string,
	rendition: string,
	build: string,
	progress: ReadingProgress,
): void {
	getStorage().set(progressKey(author, title, rendition, build), JSON.stringify(progress));
}

export function getSavedBuildSelection(
	author: string,
	title: string,
): SavedBuildSelection | null {
	const raw = getStorage().getString(buildSelectionKey(author, title));
	if (!raw) return null;
	try {
		return JSON.parse(raw) as SavedBuildSelection;
	} catch {
		return null;
	}
}

export function saveBuildSelection(
	author: string,
	title: string,
	selection: SavedBuildSelection,
): void {
	getStorage().set(buildSelectionKey(author, title), JSON.stringify(selection));
}
