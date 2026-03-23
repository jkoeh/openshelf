/** Format seconds as "Xh Ym" or "Xm" */
export function formatDuration(seconds: number): string {
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.round((seconds % 3600) / 60);
	if (hours > 0) return `${hours}h ${minutes}m`;
	return `${minutes}m`;
}

/** Format seconds as "M:SS" for player display */
export function formatTime(seconds: number): string {
	const m = Math.floor(seconds / 60);
	const s = Math.floor(seconds % 60);
	return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Deterministic hue from a string (for book card backgrounds) */
export function stringToHue(s: string): number {
	let hash = 0;
	for (let i = 0; i < s.length; i++) {
		hash = s.charCodeAt(i) + ((hash << 5) - hash);
	}
	return ((hash % 360) + 360) % 360;
}
