import { z } from "@hono/zod-openapi";
import { r2Key } from "./r2-keys";

const editionPath = "[a-z0-9]+(?:[-_][a-z0-9]+)*(?:/[a-z0-9]+(?:[-_][a-z0-9]+)*){1,5}";
export const SourceIdSchema = z.string().max(300).regex(
	new RegExp(`^(?:gutenberg:[1-9][0-9]*|standardebooks:${editionPath})$`),
);

export function validSourceEpub(url: URL, sourceId: string): boolean {
	if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash) return false;
	if (sourceId.startsWith("standardebooks:")) {
		if (!SourceIdSchema.safeParse(sourceId).success) return false;
		const path = sourceId.slice(15);
		const filename = path.split("/").map((part) => part.replaceAll("_", "-")).join("_");
		return url.hostname === "standardebooks.org" &&
			(url.search === "" || url.search === "?source=download") &&
			url.pathname === `/ebooks/${path}/downloads/${filename}.epub`;
	}
	const number = /^gutenberg:([1-9][0-9]*)$/.exec(sourceId)?.[1];
	return !!number && url.hostname === "www.gutenberg.org" && !url.search &&
		new RegExp(`^/(?:ebooks/${number}\\.epub(?:3)?(?:\\.(?:images|noimages))?|cache/epub/${number}/pg${number}(?:-images)?(?:-3)?\\.epub)$`).test(url.pathname);
}

export async function sourceTitleSlug(titleSlug: string, sourceId: string): Promise<string> {
	if (sourceId.startsWith("gutenberg:")) return `${titleSlug}-g${sourceId.slice(10)}`;
	return `${titleSlug}-se-${(await sourceDigest(sourceId)).slice(0, 12)}`;
}

async function sourceDigest(sourceId: string): Promise<string> {
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(sourceId));
	return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sourceEpubKey(sourceId: string): Promise<string> {
	return r2Key.sourceEpub(await sourceDigest(sourceId));
}
