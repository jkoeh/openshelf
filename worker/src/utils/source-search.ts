import { Converter as simplify } from "opencc-js/t2cn";
import { Converter as traditionalize } from "opencc-js/cn2t";

// Conversion is for discovery only. Never rewrite edition metadata or EPUB text.
const simplified = simplify({ from: "t", to: "cn" });
const traditional = traditionalize({ from: "cn", to: "t" });
// Share the large Simplified-to-Traditional trie; regional variant dictionaries
// are small and run after it instead of rebuilding it for every locale.
const regional = ["tw", "hk"].map((to) => traditionalize({ from: "t", to: to as "tw" | "hk" }));

export const cleanSearch = (value: string) =>
	value
		.toLowerCase()
		.normalize("NFKD")
		.replace(/[\u0300-\u036f]/g, "")
		.replace(/[^\p{L}\p{N}]+/gu, " ")
		.trim();

export function canonicalSearch(value: string): string {
	return cleanSearch(/\p{Script=Han}/u.test(value) ? simplified(value) : value);
}

export function searchForms(value: string): string[] {
	if (!/\p{Script=Han}/u.test(value)) return [cleanSearch(value)];
	const canonical = simplified(value);
	const base = traditional(canonical);
	return [
		...new Set(
			[value, canonical, base, ...regional.map((convert) => convert(base))].map(cleanSearch),
		),
	];
}

export function searchTokens(value: string): string[] {
	const cleaned = cleanSearch(value);
	const words = cleaned.split(" ").filter(Boolean);
	const bigrams = (cleaned.match(/\p{Script=Han}+/gu) ?? []).flatMap((run) => {
		const chars = Array.from(run);
		return chars.slice(0, -1).map((char, index) => char + chars[index + 1]);
	});
	return [...new Set([...words, ...bigrams])];
}

export function indexTokens(title: string, author: string): string[] {
	if (!/\p{Script=Han}/u.test(title + author))
		return [...new Set([...searchTokens(title).slice(0, 24), ...searchTokens(author).slice(0, 8)])];
	// Keep both source-script and canonical tokens within the existing 32-token cap.
	return [
		...new Set([
			...searchTokens(title).slice(0, 12),
			...searchTokens(canonicalSearch(title)).slice(0, 12),
			...searchTokens(author).slice(0, 4),
			...searchTokens(canonicalSearch(author)).slice(0, 4),
		]),
	];
}
