import { expect, test } from "@playwright/test";
import { strToU8, zipSync } from "fflate";

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
