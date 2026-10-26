import { expect, test, type Page } from "@playwright/test";
import { strToU8, zipSync } from "fflate";

const heading = "第一回　甄士隱夢幻識通靈";
const first = "此開卷第一回也，因曾歷過一番夢幻之后。";
const second = "　　列位看官：你道此書從何而來？說起根由雖近荒唐。";
const book = (index: number) => ({
	source_id: `gutenberg:${24264 + index}`,
	title: index ? `古書${index}` : "紅樓夢",
	author: "Cao Xueqin",
	language: "zh",
	reading_cached: true,
	state: "ready_to_generate",
	job_id: null,
	job_state: null,
	author_slug: null,
	title_slug: null,
});

async function fixtures(page: Page) {
	let starts = 0;
	await page.route("**/api/v1/catalog**", (route) =>
		route.fulfill({ json: { version: 2, books: [], total: 0, page: 1, limit: 20 } }),
	);
	await page.route("**/api/v1/source-books?*", (route) => {
		const query = new URL(route.request().url()).searchParams;
		const browsing = !query.get("q") && query.get("language") === "zh";
		const pageNumber = Number(query.get("page") ?? 1);
		return route.fulfill({
			json: browsing
				? {
						books: pageNumber === 1 ? Array.from({ length: 10 }, (_, i) => book(i)) : [book(10)],
						total: 11,
						page: pageNumber,
						limit: 10,
					}
				: { books: [] },
		});
	});
	const files = {
		"META-INF/container.xml":
			'<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
		"book.opf":
			'<package><metadata><language>zh-Hant</language></metadata><manifest><item id="one" href="one.xhtml"/></manifest><spine><itemref idref="one"/></spine></package>',
		"one.xhtml": `<html><body><h1>The Project Gutenberg eBook</h1>${Array.from({ length: 20 }, (_, i) => `<p>Original front matter ${i + 1} remains available.</p>`).join("")}<h1>${heading}</h1><p>此開卷第一回也，因曾歷過一番夢\n幻之后。\n${second}\n　　天地玄黃，宇宙洪荒。<br/>日月盈昃，辰宿列張。</p></body></html>`,
	};
	const bytes = zipSync(
		Object.fromEntries(Object.entries(files).map(([path, text]) => [path, strToU8(text)])),
	);
	await page.route("**/api/v1/source-books/*/epub?inline=1", (route) =>
		route.fulfill({ contentType: "application/epub+zip", body: Buffer.from(bytes) }),
	);
	await page.route("**/api/v1/generation-jobs**", (route) => {
		if (route.request().method() === "POST") starts++;
		return route.fulfill({
			json: {
				id: "existing",
				source_id: "gutenberg:24264",
				state: "completed",
				mode: "standard",
				stage: "completed",
				author_slug: "cao",
				title_slug: "hongloumeng",
			},
		});
	});
	return () => starts;
}

test("Chinese browse reveals available titles and pages without starting audio", async ({
	page,
}) => {
	const starts = await fixtures(page);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto("/");
	await page.getByRole("button", { name: "Browse Chinese books / 中文", exact: true }).click();
	await expect(page.getByRole("heading", { name: "Chinese books · 中文書庫" })).toBeVisible();
	await expect(page.getByText("11 Chinese editions ready to read", { exact: false })).toBeVisible();
	await expect(page.getByText("紅樓夢", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Read now", exact: true })).toHaveCount(10);
	await page.getByRole("button", { name: "More Chinese books" }).click();
	await expect(page.getByText("古書10", { exact: true })).toBeVisible();
	await page.getByRole("textbox", { name: "Search books" }).fill("未收錄");
	await expect(page.getByText("No matching indexed edition.", { exact: false })).toBeVisible();
	await page.getByRole("button", { name: "Clear search" }).click();
	await expect(page.getByText("紅樓夢", { exact: true })).toBeVisible();
	expect(starts()).toBe(0);
});

for (const width of [390, 1280]) {
	test(`Chinese reader preserves paragraphs and chooses Qwen at ${width}px`, async ({
		page,
	}, testInfo) => {
		const starts = await fixtures(page);
		await page.setViewportSize({ width, height: 900 });
		await page.goto("/source/gutenberg:24264?title=紅樓夢&language=zh&job=existing");
		await page.getByRole("button", { name: "Jump to first chapter / 跳至正文" }).click();
		await expect(page.getByText(first, { exact: true })).toBeVisible();
		await expect(page.getByText(second, { exact: true })).toBeVisible();
		const top = await page.getByText(first, { exact: true }).boundingBox();
		const next = await page.getByText(second, { exact: true }).boundingBox();
		expect(next!.y).toBeGreaterThan(top!.y + top!.height);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true,
		);
		await page.screenshot({ path: testInfo.outputPath("chinese-reader.png") });
		await page.getByRole("button", { name: "Start Listening", exact: true }).click();
		await expect(page).toHaveURL(/rendition=qwen3-serena/);
		expect(starts()).toBe(0);
	});
}
