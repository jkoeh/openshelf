import { expect, test } from "@playwright/test";
import type { SourceBook } from "../../types";

const edition: SourceBook = {
  source_id: "gutenberg:11", title: "Alice's Adventures in Wonderland", author: "Lewis Carroll",
  state: "ready_to_generate", job_id: null, author_slug: null, title_slug: null,
};

async function mockCatalog(page: import("@playwright/test").Page, books: SourceBook[] = [edition]) {
  await page.route("**/api/v1/catalog**", async (route) => route.fulfill({ json: {
    version: 2, generated_at: "2026-09-29", books: [], total: 0, page: 1, limit: 20,
  } }));
  await page.route("**/api/v1/source-books?*", async (route) => route.fulfill({ json: { books } }));
}

async function mockGoogleSignIn(page: import("@playwright/test").Page) {
  await page.route("https://accounts.google.com/gsi/client", async (route) => route.fulfill({
    contentType: "application/javascript",
    body: `window.google={accounts:{id:{initialize:({callback})=>{window.adminCallback=callback},renderButton:(element)=>{const button=document.createElement('button');button.textContent='Sign in with Google';button.onclick=()=>window.adminCallback({credential:'mock-google-token'});element.appendChild(button)},disableAutoSelect:()=>{}}}};`,
  }));
  await page.route("**/api/v1/admin/me", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer mock-google-token");
    await route.fulfill({ json: { email: "johnkoeh@gmail.com" } });
  });
}

test("mobile visitor requests an audiobook without a token and opens it when ready", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let createCount = 0;
  let statusCount = 0;
  await mockCatalog(page);
  await page.route("**/api/v1/generation-jobs", async (route) => {
    expect(route.request().headers().authorization).toBeUndefined();
    expect(route.request().postDataJSON()).toEqual({ source_id: "gutenberg:11" });
    createCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11",
      state: "queued", stage: "queued", author_slug: null, title_slug: null,
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/generation-jobs/job-1", async (route) => {
    expect(route.request().headers().authorization).toBeUndefined();
    statusCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11",
      state: "completed", stage: "completed", author_slug: "lewis-carroll", title_slug: "alice-g11",
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/books/lewis-carroll/alice-g11**", async (route) => route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Fixture stops at navigation" } } }));

  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByText(edition.title)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("catalog-mobile.png"), fullPage: true });
  await expect(page.getByText("No books found")).toHaveCount(0);
  await page.getByRole("button", { name: "Request audiobook" }).click();
  await expect(page.getByLabel("Owner token")).toHaveCount(0);
  await expect(page.getByText("Generation completed")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("link", { name: "Open audiobook" }).click();
  await expect(page).toHaveURL(/\/book\/lewis-carroll\/alice-g11/);
  expect(createCount).toBe(1);
  expect(statusCount).toBeGreaterThan(0);
});

test("failed long audio still offers an immediate EPUB download with its specific failure", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCatalog(page, [{ ...edition, source_id: "gutenberg:2554", title: "Crime and Punishment",
    state: "failed", job_state: "failed", job_id: "long-book-job", job_error_code: "BOOK_TOO_LONG" }]);
  const audioRequests: string[] = [];
  await page.route("**/api/v1/generation-jobs**", async (route) => {
    audioRequests.push(route.request().url());
    await route.abort();
  });
  await page.route("**/api/v1/source-books/gutenberg%3A2554/epub", async (route) => route.fulfill({
    contentType: "application/epub+zip", headers: { "Content-Disposition": 'attachment; filename="crime-and-punishment.epub"' },
    body: "PK\u0003\u0004download-fixture",
  }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("crime");
  await expect(page.getByText(/Audio generation stopped because this edition exceeds/)).toBeVisible();
  const link = page.getByRole("link", { name: "Download EPUB" });
  await expect(link).toHaveAttribute("href", /\/source-books\/gutenberg%3A2554\/epub$/);
  const downloadPromise = page.waitForEvent("download");
  await link.click();
  expect((await downloadPromise).suggestedFilename()).toBe("crime-and-punishment.epub");
  expect(audioRequests).toEqual([]);
  await expect(page.getByText("Crime and Punishment", { exact: true })).toBeVisible();
});

test("desktop shows two edition cards and keeps failed jobs out of public retry", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const second = { ...edition, source_id: "gutenberg:12", title: "Another Edition" };
  await mockCatalog(page, [edition, second]);
  let createCount = 0;
  let retryCount = 0;
  await page.route("**/api/v1/generation-jobs", async (route) => {
    createCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11",
      state: "queued", stage: "queued", author_slug: null, title_slug: null,
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/generation-jobs/job-1", async (route) => route.fulfill({ json: {
    id: "job-1", source_id: "gutenberg:11", state: "failed", stage: "failed",
    author_slug: null, title_slug: null, error_code: "RIGHTS_NOT_VERIFIED",
    created_at: "2026-09-29", updated_at: "2026-09-29",
  } }));
  await page.route("**/api/v1/generation-jobs/job-1/retry", async (route) => {
    retryCount++;
    await route.fulfill({ status: 403, json: { error: { code: "UNAUTHORIZED" } } });
  });

  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByText(edition.title)).toBeVisible();
  await expect(page.getByText(second.title)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("catalog-desktop.png"), fullPage: true });
  const first = await page.getByText(edition.title).boundingBox();
  const other = await page.getByText(second.title).boundingBox();
  expect(first).not.toBeNull();
  expect(other).not.toBeNull();
  expect(Math.abs(first!.y - other!.y)).toBeLessThan(40);
  await page.getByRole("button", { name: "Request audiobook" }).first().click();
  await expect(page.getByText(/Rights could not be verified/)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Retry generation" })).toHaveCount(0);
  await expect(page.getByLabel("Owner token")).toHaveCount(0);
  expect(createCount).toBe(1);
  expect(retryCount).toBe(0);
});

test("tablet keeps edition cards within the viewport", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 820, height: 1180 });
  await mockCatalog(page, [edition, { ...edition, source_id: "gutenberg:12", title: "Another Edition" }]);
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByText("Another Edition")).toBeVisible();
  const cards = await page.getByRole("button", { name: "Request audiobook" }).all();
  const first = await cards[0].boundingBox();
  const second = await cards[1].boundingBox();
  expect(first).not.toBeNull();
  expect(second).not.toBeNull();
  expect(Math.abs(first!.y - second!.y)).toBeLessThan(40);
  expect(second!.x + second!.width).toBeLessThanOrEqual(820);
  await page.screenshot({ path: testInfo.outputPath("catalog-tablet.png"), fullPage: true });
});

test("mobile search presents two matching editions without overflow", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const consoleErrors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await mockCatalog(page, [
    { ...edition, source_id: "gutenberg:2554", title: "Crime and Punishment", author: "Dostoyevsky, Fyodor" },
    { ...edition, source_id: "gutenberg:2760", title: "Celebrated Crimes (Complete)", author: "Dumas, Alexandre" },
  ]);
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("crime and pu");
  await expect(page.getByText("Celebrated Crimes (Complete)")).toBeVisible();
  const rightEdge = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(rightEdge).toBeLessThanOrEqual(390);
  await expect(page.getByRole("button", { name: "Request audiobook" })).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath("design-mobile.png"), fullPage: true });
  expect(consoleErrors).toEqual([]);
});

test("mobile owner sign-in unlocks cancellation without exposing a local key", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const queued = { id: "job-1", source_id: "gutenberg:11", state: "queued", stage: "queued",
    author_slug: null, title_slug: null, error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" };
  await mockCatalog(page, [{ ...edition, state: "queued", job_id: "job-1" }]);
  await mockGoogleSignIn(page);
  await page.route("**/api/v1/generation-jobs/job-1", async (route) => route.fulfill({ json: queued }));
  let canceled = false;
  await page.route("**/api/v1/generation-jobs/job-1/cancel", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer mock-google-token");
    canceled = true;
    await route.fulfill({ json: { ...queued, state: "canceled", stage: "canceled" } });
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByText(edition.title)).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel generation" })).toHaveCount(0);
  await page.getByRole("button", { name: "Owner controls" }).click();
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await expect(page.getByRole("button", { name: "Cancel generation" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel generation" }).click();
  await expect(page.getByText("Request canceled.")).toBeVisible();
  expect(canceled).toBe(true);
  await expect(page.getByLabel("Owner token")).toHaveCount(0);
});

test("mobile owner confirms an OpenAI-directed job before it is created", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCatalog(page);
  await mockGoogleSignIn(page);
  let created = 0;
  const expressive = { id: "expressive-job", source_id: "gutenberg:11", mode: "expressive",
    state: "queued", stage: "queued", author_slug: null, title_slug: null,
    error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" };
  await page.route("**/api/v1/generation-jobs", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer mock-google-token");
    expect(route.request().postDataJSON()).toEqual({
      source_id: "gutenberg:11", mode: "expressive", regenerate: false,
    });
    created++;
    await route.fulfill({ json: expressive });
  });
  await page.route("**/api/v1/generation-jobs/expressive-job", async (route) =>
    route.fulfill({ json: expressive }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByRole("button", { name: "Generate expressive audio" })).toHaveCount(0);
  await page.getByRole("button", { name: "Owner controls" }).click();
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "Generate expressive audio" }).click();
  await expect(page.getByText(/uses OpenAI emotion direction/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("expressive-confirm-mobile.png"), fullPage: true });
  expect(created).toBe(0);
  await page.getByRole("button", { name: "Start expressive job" }).click();
  await expect(page.getByText("Expressive generation queued")).toBeVisible();
  expect(created).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("owner can start expressive regeneration after standard completion reaches polling before search", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCatalog(page, [{ ...edition, job_state: "queued", job_id: "standard-job" }]);
  await mockGoogleSignIn(page);
  await page.route("**/api/v1/generation-jobs/standard-job", async (route) =>
    route.fulfill({ json: {
      id: "standard-job", source_id: "gutenberg:11", mode: "standard", state: "completed",
      stage: "completed", author_slug: "lewis-carroll", title_slug: "alice-g11",
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29",
    } }));
  let created = 0;
  await page.route("**/api/v1/generation-jobs", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer mock-google-token");
    expect(route.request().postDataJSON()).toEqual({
      source_id: "gutenberg:11", mode: "expressive", regenerate: true,
    });
    created++;
    await route.fulfill({ json: {
      id: "expressive-job", source_id: "gutenberg:11", mode: "expressive",
      state: "queued", stage: "queued", author_slug: "lewis-carroll", title_slug: "alice-g11",
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29",
    } });
  });
  await page.route("**/api/v1/generation-jobs/expressive-job", async (route) =>
    route.fulfill({ json: {
      id: "expressive-job", source_id: "gutenberg:11", mode: "expressive",
      state: "queued", stage: "queued", author_slug: "lewis-carroll", title_slug: "alice-g11",
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29",
    } }));

  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByRole("link", { name: "Open audiobook" })).toBeVisible();
  await page.getByRole("button", { name: "Owner controls" }).click();
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "Generate expressive audio" }).click();
  await page.getByRole("button", { name: "Start expressive job" }).click();
  await expect(page.getByText("Expressive generation queued")).toBeVisible();
  expect(created).toBe(1);
});

test("published audiobook remains playable while owner retries failed regeneration", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockCatalog(page, [{ ...edition, state: "available", job_state: "failed", job_id: "job-2",
    author_slug: "lewis-carroll", title_slug: "alice-g11" }]);
  await mockGoogleSignIn(page);
  await page.route("**/api/v1/generation-jobs/job-2/retry", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer mock-google-token");
    await route.fulfill({ json: { id: "job-2", source_id: "gutenberg:11", state: "queued",
      stage: "queued", author_slug: "lewis-carroll", title_slug: "alice-g11",
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/generation-jobs/job-2", async (route) => route.fulfill({ json: {
    id: "job-2", source_id: "gutenberg:11", state: "queued", stage: "queued",
    author_slug: "lewis-carroll", title_slug: "alice-g11", error_code: null,
    created_at: "2026-09-29", updated_at: "2026-09-29",
  } }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "Search books" }).fill("alice");
  await expect(page.getByRole("link", { name: "Open audiobook" })).toBeVisible();
  await page.getByRole("button", { name: "Owner controls" }).click();
  await page.getByRole("button", { name: "Sign in with Google" }).click();
  await page.getByRole("button", { name: "Retry generation" }).click();
  await expect(page.getByText("Standard generation queued")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open audiobook" })).toBeVisible();
});
