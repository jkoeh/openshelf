import { unzipSync, strFromU8 } from "fflate";
import { XMLParser } from "fast-xml-parser";

export interface SourceSection {
	title: string;
	paragraphs: string[];
}
const xml = new XMLParser({ ignoreAttributes: false, removeNSPrefix: true, htmlEntities: true });
const list = <T>(value: T | T[] | undefined): T[] =>
	value === undefined ? [] : Array.isArray(value) ? value : [value];

// EPUB XHTML is data only: never inject HTML or execute scripts in the reader.
export function parseSourceEpub(bytes: Uint8Array): SourceSection[] {
	if (bytes.length > 32 * 1024 * 1024)
		throw new Error("This EPUB is too large to open here.");
	let expanded = 0;
	const files = unzipSync(bytes, {
		filter: (entry) => {
			expanded += entry.originalSize;
			if (expanded > 64 * 1024 * 1024)
				throw new Error("This EPUB is too large to open here.");
			return /\.(xml|opf|xhtml|html|htm)$/i.test(entry.name);
		},
	});
	const read = (path: string) => {
		if (!files[path])
			throw new Error("This EPUB is missing its reading content.");
		return strFromU8(files[path]);
	};
	const container = xml.parse(read("META-INF/container.xml"));
	const opfPath = list<any>(container.container?.rootfiles?.rootfile)[0]?.[
		"@_full-path"
	];
	if (!opfPath) throw new Error("This EPUB has no reading order.");
	const pkg = xml.parse(read(opfPath)).package;
	const items = list<any>(pkg.manifest?.item);
	const base = opfPath.includes("/")
		? opfPath.slice(0, opfPath.lastIndexOf("/") + 1)
		: "";
	const resolve = (href: string) => {
		const parts: string[] = [];
		for (const part of (base + decodeURIComponent(href.split("#")[0])).split(
			"/",
		)) {
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
						if (key === "br") return " ";
						return key === "#text"
							? String(value)
							: Array.isArray(value)
								? text(value)
								: "";
					})
					.join(""),
			)
			.join("");
	}
	function blocks(nodes: any[], output: string[]) {
		for (const node of nodes)
			for (const [key, value] of Object.entries(node)) {
				if (["script", "style"].includes(key)) continue;
				if (Array.isArray(value)) {
					if (/^(p|h[1-6]|li|blockquote|pre)$/.test(key)) {
						const line = text(value).replace(/\s+/g, " ").trim();
						if (line) output.push(line);
					} else blocks(value, output);
				}
			}
	}
	const sections = list<any>(pkg.spine?.itemref)
		.filter((ref) => ref["@_linear"] !== "no")
		.map((ref) => {
			const item = items.find((item) => item["@_id"] === ref["@_idref"]);
			if (!item) throw new Error("This EPUB has an incomplete reading order.");
			const paragraphs: string[] = [];
			blocks(textParser.parse(read(resolve(item["@_href"]))), paragraphs);
			return { title: paragraphs[0] ?? "Section", paragraphs };
		})
		.filter((section) => section.paragraphs.length);
	if (!sections.length) throw new Error("No readable text found in this EPUB.");
	return sections;
}
