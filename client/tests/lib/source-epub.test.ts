import { describe, expect, it } from "vitest";
import { strToU8, zipSync } from "fflate";
import { parseSourceEpub } from "../../lib/source-epub";
import { audioCreation } from "../../lib/audio-creation";
import type { GenerationJob } from "../../types";

describe("immediate reading", () => {
	it("reads spine order, resolves relative paths, decodes entities and excludes scripts", () => {
		const bytes = zipSync(
			Object.fromEntries(
				Object.entries({
					"META-INF/container.xml":
						'<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
					"OPS/book.opf":
						'<package><manifest><item id="a" href="text/a.xhtml"/><item id="b" href="../b.xhtml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
					"OPS/text/a.xhtml":
						"<html><body><h1>Later</h1><p>A <em>quiet</em> page.</p></body></html>",
					"b.xhtml":
						"<html><body><h1>First</h1><script>bad()</script><p>Tea &amp; biscuits.</p></body></html>",
				}).map(([name, text]) => [name, strToU8(text)]),
			),
		);
		expect(parseSourceEpub(bytes)).toEqual([
			{ title: "First", paragraphs: ["First", "Tea & biscuits."] },
			{ title: "Later", paragraphs: ["Later", "A quiet page."] },
		]);
	});
	it("rejects invalid books with no reading content", () => {
		expect(() => parseSourceEpub(zipSync({}))).toThrow(/missing/);
	});
	it("preserves Chinese script, inline punctuation and edition spine order", () => {
		const files = {
			"META-INF/container.xml": '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
			"book.opf": '<package><manifest><item id="two" href="second.xhtml"/><item id="ten" href="tenth.xhtml"/></manifest><spine><itemref idref="ten"/><itemref idref="two"/></spine></package>',
			"second.xhtml": '<html><body><h1>第二回</h1><p>宇宙洪荒。</p></body></html>',
			"tenth.xhtml": '<html><body><h1>第十回</h1><p>天地<em>玄黃</em>，你好！</p></body></html>',
		};
		const bytes = zipSync(Object.fromEntries(Object.entries(files).map(([name, text]) => [name, strToU8(text)])));
		expect(parseSourceEpub(bytes)).toEqual([
			{ title: "第十回", paragraphs: ["第十回", "天地玄黃，你好！"] },
			{ title: "第二回", paragraphs: ["第二回", "宇宙洪荒。"] },
		]);
	});
	it("shows stage milestones without claiming completion before publication", () => {
		const job = { state: "running", stage: "download" } as GenerationJob;
		expect(audioCreation(null).percent).toBe(0);
		expect(audioCreation(job).percent).toBe(25);
		expect(audioCreation({ ...job, stage: "upload" }).percent).toBe(50);
		expect(audioCreation({ ...job, state: "completed" }).percent).toBe(100);
		expect(audioCreation({ ...job, state: "failed" }).detail).toContain(
			"keep reading",
		);
	});
});
