import type { Manifest, ManifestRendition } from "../types";

const DEFAULT_RENDITION = "kokoro-af-heart";

export function selectRendition(
	manifest: Manifest,
	requested?: string,
): { key: string; rendition: ManifestRendition } | null {
	if (requested && manifest.renditions[requested]) {
		return { key: requested, rendition: manifest.renditions[requested] };
	}

	if (manifest.renditions[DEFAULT_RENDITION]) {
		return { key: DEFAULT_RENDITION, rendition: manifest.renditions[DEFAULT_RENDITION] };
	}

	const firstKey = Object.keys(manifest.renditions).sort()[0];
	return firstKey ? { key: firstKey, rendition: manifest.renditions[firstKey] } : null;
}
