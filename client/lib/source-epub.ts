import { unzipSync, strFromU8 } from "fflate";
import { XMLParser } from "fast-xml-parser";

export interface SourceSection {
	title: string;
	paragraphs: string[];
	language?: "en" | "zh";
}
const xml = new XMLParser({ ignoreAttributes: false, removeNSPrefix: true, htmlEntities: true });
const list = <T>(value: T | T[] | undefined): T[] =>
	value === undefined ? [] : Array.isArray(value) ? value : [value];

// EPUB XHTML is data only: never inject HTML or execute scripts in the reader.
export function parseSourceEpub(bytes: Uint8Array, languageHint?: string): SourceSection[] {
	if (bytes.length > 32 * 1024 * 1024) throw new Error("This EPUB is too large to open here.");
	let expanded = 0;
	const files = unzipSync(bytes, {
		filter: (entry) => {
			expanded += entry.originalSize;
			if (expanded > 64 * 1024 * 1024) throw new Error("This EPUB is too large to open here.");
			return /\.(xml|opf|xhtml|html|htm)$/i.test(entry.name);
		},
	});
	const read = (path: string) => {
		if (!files[path]) throw new Error("This EPUB is missing its reading content.");
		return strFromU8(files[path]);
	};
	const container = xml.parse(read("META-INF/container.xml"));
	const opfPath = list<any>(container.container?.rootfiles?.rootfile)[0]?.["@_full-path"];
	if (!opfPath) throw new Error("This EPUB has no reading order.");
	const pkg = xml.parse(read(opfPath)).package;
	const language = (value: unknown): "en" | "zh" | undefined => {
		const code = String(value ?? "")
			.toLowerCase()
			.split(/[-_]/)[0];
		return code === "zh" || code === "en" ? code : undefined;
	};
	const opfLanguage = list<any>(pkg.metadata?.language)[0];
	const bookLanguage =
		language(typeof opfLanguage === "object" ? opfLanguage?.["#text"] : opfLanguage) ??
		language(languageHint);
	const items = list<any>(pkg.manifest?.item);
	const base = opfPath.includes("/") ? opfPath.slice(0, opfPath.lastIndexOf("/") + 1) : "";
	const resolve = (href: string) => {
		const parts: string[] = [];
		for (const part of (base + decodeURIComponent(href.split("#")[0])).split("/")) {
			if (part === "..") parts.pop();
			else if (part && part !== ".") parts.push(part);
		}
		return parts.join("/");
	};
	const textParser = new XMLParser({
		ignoreAttributes: true,
		removeNSPrefix: true,
		preserveOrder: true,
		trimValues: false,
		htmlEntities: true,
	});
	function text(nodes: any[]): string {
		return nodes
			.map((node) =>
				Object.entries(node)
					.map(([key, value]) => {
						if (["script", "style", "@attrs"].includes(key)) return "";
						// NUL is not legal XML text; use it only as an internal explicit-break marker.
						if (key === "br") return "\0";
						return key === "#text" ? String(value) : Array.isArray(value) ? text(value) : "";
					})
					.join(""),
			)
			.join("");
	}
	function blocks(nodes: any[], output: { raw: string; pre: boolean }[]) {
		for (const node of nodes)
			for (const [key, value] of Object.entries(node)) {
				if (["script", "style"].includes(key)) continue;
				if (Array.isArray(value)) {
					if (/^(p|h[1-6]|li|blockquote|pre)$/.test(key)) {
						const raw = text(value);
						if (raw.trim()) output.push({ raw, pre: key === "pre" });
					} else blocks(value, output);
				}
			}
	}
	function paragraphs(raw: string, chinese: boolean, pre: boolean): string[] {
		if (pre)
			return [
				raw
					.replace(/\r\n?/g, "\n")
					.replaceAll("\0", "\n")
					.replace(/^[\r\n]+|[\r\n]+$/g, ""),
			];
		if (!chinese)
			return [
				raw
					.replace(/[ \t\r\n]+/g, " ")
					.trim()
					.replaceAll("\0", "\n"),
			];
		const cjk = /[\p{Script=Han}\u3000-\u303f\uff00-\uffef]/u;
		// Legacy text-derived EPUBs put many indented Chinese paragraphs inside one <p>.
		return raw
			.replace(/\r\n?/g, "\n")
			.replace(/(^|\n)([—─_=*-]{4,})\n/g, "$1$2\n\n")
			.split(/\n(?=[\u3000]|[ \t]{2,}\S)|\n{2,}/u)
			.map((part) =>
				part
					.replace(/[ \t]*\n[ \t]*/g, (match, offset, whole) =>
						cjk.test(whole[offset - 1] ?? "") || cjk.test(whole[offset + match.length] ?? "")
							? ""
							: " ",
					)
					.replace(/^[ \t]+|[ \t]+$/g, "")
					.replaceAll("\0", "\n"),
			)
			.filter((part) => part.trim().length > 0);
	}
	const sections = list<any>(pkg.spine?.itemref)
		.filter((ref) => ref["@_linear"] !== "no")
		.map((ref) => {
			const item = items.find((item) => item["@_id"] === ref["@_idref"]);
			if (!item) throw new Error("This EPUB has an incomplete reading order.");
			const content: { raw: string; pre: boolean }[] = [];
			blocks(textParser.parse(read(resolve(item["@_href"]))), content);
			const allText = content.map((block) => block.raw).join("");
			const inferred =
				(allText.match(/\p{Script=Han}/gu)?.length ?? 0) > (allText.match(/[a-z]/gi)?.length ?? 0);
			const sectionLanguage = bookLanguage ?? (inferred ? "zh" : undefined);
			const lines = content.flatMap((block) =>
				paragraphs(block.raw, sectionLanguage === "zh", block.pre),
			);
			return {
				title: lines[0] ?? "Section",
				paragraphs: lines,
				...(sectionLanguage ? { language: sectionLanguage } : {}),
			};
		})
		.filter((section) => section.paragraphs.length);
	if (!sections.length) throw new Error("No readable text found in this EPUB.");
	return sections;
}
