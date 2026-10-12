import { describe, expect, it } from "vitest";
import { parseStandardEbooksSearch, readBoundedSource, standardEbooksEpubUrl } from "../../lib/standard-ebooks";

describe("official Standard Ebooks data", () => {
	it("excludes unavailable placeholders, invalid paths and duplicate editions", () => {
		const entry = (attrs: string, title = "Alice &amp; Bob") => `<li typeof="schema:Book" ${attrs}><span property="schema:name">${title}</span><span property="schema:name">An Author</span></li>`;
		const books = parseStandardEbooksSearch(entry('about="/ebooks/author/book"') + entry('about="/ebooks/author/book"') + entry('class="future not-pd" about="/ebooks/author/future"') + entry('about="/ebooks/author/../escape"'));
		expect(books).toHaveLength(1);
		expect(books[0]).toMatchObject({ title: "Alice & Bob", indexed: false });
	});
	it("preserves contributor identity and the compatible filename convention", () => {
		expect(standardEbooksEpubUrl("standardebooks:author/book/translator-one_translator-two")).toContain("author_book_translator-one-translator-two.epub?source=download");
	});
	it("cancels a chunked body once it exceeds the reading limit", async () => {
		let canceled = false;
		const body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(5)); }, cancel() { canceled = true; } });
		await expect(readBoundedSource(new Response(body), 4)).rejects.toThrow("too large");
		expect(canceled).toBe(true);
	});
});
