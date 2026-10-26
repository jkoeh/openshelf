import { expect, test } from "@playwright/test";
import { strToU8, zipSync } from "fflate";

test("missing editions use only our database without upstream discovery requests", async ({ page }) => {
  await page.route("**/api/v1/catalog**", route => route.fulfill({ json: { version: 2, books: [], total: 0, page: 1, limit: 20 } }));
  await page.route("**/api/v1/source-books?*", route => route.fulfill({ json: { books: [] } }));
  const upstream: string[] = [];
  await page.route("https://standardebooks.org/**", route => { upstream.push(route.request().url()); return route.abort(); });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("demons");
  await expect(page.getByText("No matching indexed edition.", { exact: false })).toBeVisible();
  expect(upstream).toEqual([]);
});

test("legacy unindexed Standard Ebooks reading links still use the exact EPUB fallback", async ({ page }) => {
  const sourceId = "standardebooks:fyodor-dostoevsky/demons/constance-garnett";
  await page.route("**/api/v1/catalog**", route => route.fulfill({ json: { version: 2, books: [], total: 0, page: 1, limit: 20 } }));
  await page.route("**/api/v1/source-books?*", route => route.fulfill({ json: { books: [] } }));
  await page.route("https://standardebooks.org/ebooks?query=*", route => {
    expect(route.request().headers().authorization).toBeUndefined();
    return route.fulfill({ contentType: "application/xhtml+xml", headers: { "Access-Control-Allow-Origin": "*" }, body: `<li typeof="schema:Book" about="/ebooks/fyodor-dostoevsky/demons/constance-garnett"><span property="schema:name">Demons</span><span property="schema:name">Fyodor Dostoevsky</span></li>` });
  });
  await page.route("**/api/v1/source-books/*/epub?inline=1", route => route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Not indexed" } } }));
  const epub = zipSync(Object.fromEntries(Object.entries({
    "META-INF/container.xml": '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
    "book.opf": '<package><manifest><item id="one" href="one.xhtml"/></manifest><spine><itemref idref="one"/></spine></package>',
    "one.xhtml": '<html><body><h1>Demons</h1><p>Read Demons directly from Standard Ebooks.</p></body></html>',
  }).map(([path, text]) => [path, strToU8(text)])));
  await page.route("https://standardebooks.org/ebooks/**/downloads/*.epub?source=download", route => {
    expect(route.request().url()).toContain("fyodor-dostoevsky_demons_constance-garnett.epub");
    expect(route.request().headers().authorization).toBeUndefined();
    return route.fulfill({ contentType: "application/epub+zip", headers: { "Access-Control-Allow-Origin": "*" }, body: Buffer.from(epub) });
  });
  let jobs = 0;
  await page.route("**/api/v1/generation-jobs**", route => { jobs++; return route.abort(); });
  await page.goto(`/source/${encodeURIComponent(sourceId)}?title=Demons&author=Fyodor%20Dostoevsky&indexed=0`);
  expect(decodeURIComponent(page.url())).toContain(`/source/${sourceId}`);
  await expect(page.getByText("Read Demons directly from Standard Ebooks.")).toBeVisible();
  await expect(page.getByText(/Audio can be prepared after this edition/)).toBeVisible();
  expect(jobs).toBe(0);
});

test("Standard Ebooks edition opens text and requests audio for the exact edition", async ({ page }) => {
  const sourceId = "standardebooks:lewis-carroll/alice/john-tenniel";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/v1/catalog**", route => route.fulfill({ json: {
    version: 2, books: [], total: 0, page: 1, limit: 20,
  } }));
  await page.route("**/api/v1/source-books?*", route => route.fulfill({ json: { books: [{
    source_id: sourceId, title: "Alice", author: "Lewis Carroll", state: "ready_to_generate",
    job_id: null, author_slug: null, title_slug: null,
  }] } }));
  const epub = zipSync(Object.fromEntries(Object.entries({
    "META-INF/container.xml": '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
    "book.opf": '<package><manifest><item id="one" href="one.xhtml"/></manifest><spine><itemref idref="one"/></spine></package>',
    "one.xhtml": '<html><body><h1>Alice</h1><p>Read this Standard Ebooks edition.</p></body></html>',
  }).map(([path, text]) => [path, strToU8(text)])));
  let epubRead = false;
  let audioRequested = false;
  await page.route("**/api/v1/source-books/*/epub?inline=1", route => {
    expect(decodeURIComponent(new URL(route.request().url()).pathname)).toContain(sourceId);
    epubRead = true;
    return route.fulfill({ contentType: "application/epub+zip", body: Buffer.from(epub) });
  });
  await page.route("**/api/v1/generation-jobs", route => {
    expect(route.request().postDataJSON()).toEqual({ source_id: sourceId });
    audioRequested = true;
    return route.fulfill({ status: 429, json: { error: { code: "QUEUE_FULL", message: "Queue full" } } });
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByText("Standard Ebooks", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Read now" }).click();
  await expect(page.getByText("Read this Standard Ebooks edition.")).toBeVisible();
  expect(epubRead).toBe(true);
  await expect.poll(() => audioRequested).toBe(true);
});
