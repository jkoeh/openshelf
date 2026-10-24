import { z } from "@hono/zod-openapi";
import { r2Key } from "./r2-keys";

const editionPath = "[a-z0-9]+(?:[-_][a-z0-9]+)*(?:/[a-z0-9]+(?:[-_][a-z0-9]+)*){1,5}";
export const SourceIdSchema = z.string().max(300).regex(
	new RegExp(`^(?:gutenberg:[1-9][0-9]*|standardebooks:${editionPath}|wikisource:(?:en|zh):[1-9][0-9]*:[1-9][0-9]*:[a-f0-9]{16})$`),
);

export function sourceProvider(sourceId: string): string {
	return sourceId.split(":")[0];
}

export function gutenbergMirrorUrl(url: URL, sourceId: string): URL | null {
	const number = /^gutenberg:([1-9][0-9]*)$/.exec(sourceId)?.[1];
	if (!number || url.protocol !== "https:" || url.username || url.password || url.port || url.hash || url.search ||
		!["www.gutenberg.org", "gutenberg.org", "dev.gutenberg.org", "gutenberg.pglaf.org"].includes(url.hostname)) return null;
	const variant = new RegExp(`^/ebooks/${number}\\.epub(3)?(?:\\.(images|noimages))?$`).exec(url.pathname);
	let path = url.pathname;
	if (variant) path = `/cache/epub/${number}/pg${number}${variant[2] === "images" ? "-images" : ""}${variant[1] ? "-3" : ""}.epub`;
	else if (!new RegExp(`^/cache/epub/${number}/pg${number}(?:-images)?(?:-3)?\\.epub$`).test(path)) return null;
	return new URL(path, "https://gutenberg.pglaf.org");
}

export function validSourceEpub(url: URL, sourceId: string): boolean {
	if (url.protocol !== "https:" || url.username || url.password || url.port || url.hash) return false;
	if (sourceId.startsWith("wikisource:")) {
		if (!SourceIdSchema.safeParse(sourceId).success) return false;
		const [, language, , revision] = sourceId.split(":");
		return url.hostname === `${language}.wikisource.org` && url.pathname === "/w/index.php" && url.search === `?oldid=${revision}`;
	}
	if (sourceId.startsWith("standardebooks:")) {
		if (!SourceIdSchema.safeParse(sourceId).success) return false;
		const path = sourceId.slice(15);
		const filename = path.split("/").map((part) => part.replaceAll("_", "-")).join("_");
		return url.hostname === "standardebooks.org" &&
			(url.search === "" || url.search === "?source=download") &&
			url.pathname === `/ebooks/${path}/downloads/${filename}.epub`;
	}
	return gutenbergMirrorUrl(url, sourceId) !== null;
}

export async function sourceTitleSlug(titleSlug: string, sourceId: string): Promise<string> {
	titleSlug ||= "untitled";
	if (sourceId.startsWith("gutenberg:")) return `${titleSlug}-g${sourceId.slice(10)}`;
	return `${titleSlug}-${sourceId.startsWith("wikisource:") ? "ws" : "se"}-${(await sourceDigest(sourceId)).slice(0, 12)}`;
}

async function sourceDigest(sourceId: string): Promise<string> {
	const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(sourceId));
	return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function sourceEpubKey(sourceId: string): Promise<string> {
	return r2Key.sourceEpub(await sourceDigest(sourceId), sourceProvider(sourceId));
}
