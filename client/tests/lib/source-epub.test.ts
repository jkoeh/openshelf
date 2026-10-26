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
			"META-INF/container.xml":
				'<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
			"book.opf":
				'<package><manifest><item id="two" href="second.xhtml"/><item id="ten" href="tenth.xhtml"/></manifest><spine><itemref idref="ten"/><itemref idref="two"/></spine></package>',
			"second.xhtml": "<html><body><h1>第二回</h1><p>宇宙洪荒。</p></body></html>",
			"tenth.xhtml": "<html><body><h1>第十回</h1><p>天地<em>玄黃</em>，你好！</p></body></html>",
		};
		const bytes = zipSync(
			Object.fromEntries(Object.entries(files).map(([name, text]) => [name, strToU8(text)])),
		);
		expect(parseSourceEpub(bytes)).toEqual([
			{ title: "第十回", paragraphs: ["第十回", "天地玄黃，你好！"], language: "zh" },
			{ title: "第二回", paragraphs: ["第二回", "宇宙洪荒。"], language: "zh" },
		]);
	});
	it("restores indented legacy Chinese paragraphs without spaces inside hard-wrapped words", () => {
		const body =
			"<h1>第一回</h1><p>——————\n此開卷第一回也，因曾歷過一番夢\n幻之后。\n　　列位看官：你道此書從何而來？\n說起根由雖近荒唐。\n　　Hello\nworld，中文<em>原文</em>不變。<br/>下一行。</p>";
		const bytes = readingFixture(body, "zh-Hant");
		expect(parseSourceEpub(bytes)[0]).toEqual({
			title: "第一回",
			language: "zh",
			paragraphs: [
				"第一回",
				"——————",
				"此開卷第一回也，因曾歷過一番夢幻之后。",
				"　　列位看官：你道此書從何而來？說起根由雖近荒唐。",
				"　　Hello world，中文原文不變。\n下一行。",
			],
		});
	});
	it("retains verse breaks and preformatted whitespace without running EPUB scripts", () => {
		const bytes = readingFixture(
			"<h1>Verse</h1><p>First line.<br/>Second line.</p><pre>  exact\n    spacing</pre><script>bad()</script>",
			"en",
		);
		expect(parseSourceEpub(bytes)[0]).toEqual({
			title: "Verse",
			language: "en",
			paragraphs: ["Verse", "First line.\nSecond line.", "  exact\n    spacing"],
		});
	});
	it("uses OPF language before a URL hint", () => {
		expect(parseSourceEpub(readingFixture("<p>English text.</p>", "en"), "zh")[0].language).toBe(
			"en",
		);
	});
	it("shows stage milestones without claiming completion before publication", () => {
		const job = { state: "running", stage: "download" } as GenerationJob;
		expect(audioCreation(null).percent).toBe(0);
		expect(audioCreation(job).percent).toBe(25);
		expect(audioCreation({ ...job, stage: "upload" }).percent).toBe(50);
		expect(audioCreation({ ...job, state: "completed" }).percent).toBe(100);
		expect(audioCreation({ ...job, state: "failed" }).detail).toContain("keep reading");
	});
});

function readingFixture(body: string, language: string) {
	return zipSync(
		Object.fromEntries(
			Object.entries({
				"META-INF/container.xml":
					'<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
				"book.opf": `<package><metadata><language>${language}</language></metadata><manifest><item id="one" href="one.xhtml"/></manifest><spine><itemref idref="one"/></spine></package>`,
				"one.xhtml": `<html><body>${body}</body></html>`,
			}).map(([path, text]) => [path, strToU8(text)]),
		),
	);
}
