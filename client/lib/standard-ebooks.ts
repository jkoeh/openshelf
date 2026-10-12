import { XMLParser } from "fast-xml-parser";
import type { SourceBook } from "../types";

const origin = "https://standardebooks.org";
const editionPattern = /^[a-z0-9]+(?:[-_][a-z0-9]+)*(?:\/[a-z0-9]+(?:[-_][a-z0-9]+)*){1,5}$/;

export function standardEbooksEpubUrl(sourceId: string): string {
	const path = sourceId.slice("standardebooks:".length);
	if (!sourceId.startsWith("standardebooks:") || sourceId.length > 300 || !editionPattern.test(path))
		throw new Error("Invalid Standard Ebooks edition.");
	const filename = path.split("/").map(part => part.replaceAll("_", "-")).join("_");
	return `${origin}/ebooks/${path}/downloads/${filename}.epub?source=download`;
}

export async function readBoundedSource(response: Response, maximum: number): Promise<Uint8Array> {
	if (Number(response.headers.get("Content-Length")) > maximum) {
		await response.body?.cancel();
		throw new Error("This source file is too large to open here.");
	}
	if (!response.body?.getReader) {
		const bytes = new Uint8Array(await response.arrayBuffer());
		if (bytes.length > maximum) throw new Error("This source file is too large to open here.");
		return bytes;
	}
	const reader = response.body.getReader();
	const chunks: Uint8Array[] = [];
	let total = 0;
	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			total += value.length;
			if (total > maximum) {
				await reader.cancel();
				throw new Error("This source file is too large to open here.");
			}
			chunks.push(value);
		}
	} finally { reader.releaseLock(); }
	const bytes = new Uint8Array(total);
	let offset = 0;
	for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
	return bytes;
}

export function parseStandardEbooksSearch(html: string): SourceBook[] {
	const books: SourceBook[] = [];
	const seen = new Set<string>();
	const xml = new XMLParser({ htmlEntities: true });
	const text = (value: string): string => String(xml.parse(`<text>${value.replace(/<[^>]*>/g, "")}</text>`).text ?? "").replace(/\s+/g, " ").trim();
	for (const [, attrs, body] of html.matchAll(/<li\b([^>]*)>([\s\S]*?)<\/li>/gi)) {
		if (!/\btypeof=["']schema:Book["']/.test(attrs)) continue;
		const classes = /\bclass=["']([^"']*)["']/.exec(attrs)?.[1].split(/\s+/) ?? [];
		if (classes.includes("not-pd")) continue;
		const path = /\babout=["']\/ebooks\/([^"']+)["']/.exec(attrs)?.[1];
		if (!path) continue;
		const sourceId = `standardebooks:${path}`;
		try { standardEbooksEpubUrl(sourceId); } catch { continue; }
		const names = [...body.matchAll(/<span\b[^>]*\bproperty=["']schema:name["'][^>]*>([\s\S]*?)<\/span>/gi)];
		if (names.length < 2 || seen.has(sourceId)) continue;
		const title = text(names[0][1]), author = text(names[1][1]);
		if (!title || title.length > 300 || !author || author.length > 200) continue;
		seen.add(sourceId);
		books.push({ source_id: sourceId, title, author, indexed: false,
			state: "ready_to_generate", job_id: null, author_slug: null, title_slug: null });
		if (books.length === 10) break;
	}
	return books;
}

export async function searchStandardEbooks(query: string, signal?: AbortSignal): Promise<SourceBook[]> {
	const response = await fetch(`${origin}/ebooks?query=${encodeURIComponent(query)}`, {
		signal, credentials: "omit", redirect: "error",
	});
	if (!response.ok) {
		await response.body?.cancel();
		throw new Error(response.status === 429 ? "Standard Ebooks is limiting requests. Try again later." : "Live Standard Ebooks search is unavailable. Try again later.");
	}
	const bytes = await readBoundedSource(response, 2 * 1024 * 1024);
	return parseStandardEbooksSearch(new TextDecoder().decode(bytes));
}
