import { expect, test } from "@playwright/test";

test("searches an edition, creates one owner job, and opens the completed audiobook", async ({ page }) => {
  let createCount = 0;
  let statusCount = 0;
  await page.route("**/api/v1/catalog**", async (route) => route.fulfill({ json: {
    version: 2, generated_at: "2026-09-29", books: [], total: 0, page: 1, limit: 20,
  } }));
  await page.route("**/api/v1/source-books**", async (route) => route.fulfill({ json: { books: [{
    source_id: "gutenberg:11", title: "Alice's Adventures in Wonderland", author: "Lewis Carroll",
    state: "ready_to_generate", job_id: null, author_slug: null, title_slug: null,
  }] } }));
  await page.route("**/api/v1/generation-jobs", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer test-owner-token-longer-than-twenty-four");
    expect(route.request().postDataJSON()).toEqual({ source_id: "gutenberg:11" });
    createCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11", build_id: "1234567890abcdef",
      state: "queued", stage: "queued", attempts: 0, author_slug: null, title_slug: null,
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/generation-jobs/job-1", async (route) => {
    statusCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11", build_id: "1234567890abcdef",
      state: "completed", stage: "completed", attempts: 1, author_slug: "lewis-carroll", title_slug: "alice-g11",
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/books/lewis-carroll/alice-g11**", async (route) => route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Fixture stops at navigation" } } }));

  await page.goto("/");
  await page.getByPlaceholder("Search books...").fill("alcie");
  await expect(page.getByText("Alice's Adventures in Wonderland")).toBeVisible();
  await page.getByRole("button", { name: "Generate audio" }).click();
  await expect(page.getByLabel("Owner token")).toBeVisible();
  await page.getByLabel("Owner token").fill("test-owner-token-longer-than-twenty-four");
  await page.getByRole("button", { name: "Submit generation job" }).click();
  await expect(page.getByText("Generation queued")).toBeVisible();
  await expect(page.getByText("Generation completed")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("link", { name: "Open finished audiobook" }).click();
  await expect(page).toHaveURL(/\/book\/lewis-carroll\/alice-g11/);
  expect(createCount).toBe(1);
  expect(statusCount).toBeGreaterThan(0);
});

test("keeps retry available after rights verification fails", async ({ page }) => {
  let createCount = 0;
  let retryCount = 0;
  await page.route("**/api/v1/catalog**", async (route) => route.fulfill({ json: {
    version: 2, generated_at: "2026-09-29", books: [], total: 0, page: 1, limit: 20,
  } }));
  await page.route("**/api/v1/source-books**", async (route) => route.fulfill({ json: { books: [{
    source_id: "gutenberg:11", title: "Alice's Adventures in Wonderland", author: "Lewis Carroll",
    state: "ready_to_generate", job_id: null, author_slug: null, title_slug: null,
  }] } }));
  await page.route("**/api/v1/generation-jobs", async (route) => {
    createCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11", build_id: "1234567890abcdef",
      state: "queued", stage: "queued", attempts: 0, author_slug: null, title_slug: null,
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });
  await page.route("**/api/v1/generation-jobs/job-1", async (route) => route.fulfill({ json: {
    id: "job-1", source_id: "gutenberg:11", build_id: "1234567890abcdef",
    state: "failed", stage: "failed", attempts: 1, author_slug: null, title_slug: null,
    error_code: "RightsNotVerified", created_at: "2026-09-29", updated_at: "2026-09-29",
  } }));
  await page.route("**/api/v1/generation-jobs/job-1/retry", async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer test-owner-token-longer-than-twenty-four");
    retryCount++;
    await route.fulfill({ json: { id: "job-1", source_id: "gutenberg:11", build_id: "1234567890abcdef",
      state: "queued", stage: "queued", attempts: 1, author_slug: null, title_slug: null,
      error_code: null, created_at: "2026-09-29", updated_at: "2026-09-29" } });
  });

  await page.goto("/");
  await page.getByPlaceholder("Search books...").fill("alice");
  await page.getByRole("button", { name: "Generate audio" }).click();
  await page.getByLabel("Owner token").fill("test-owner-token-longer-than-twenty-four");
  await page.getByRole("button", { name: "Submit generation job" }).click();
  await expect(page.getByText("Generation failed")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Rights could not be verified/)).toBeVisible();
  await page.getByRole("button", { name: "Retry generation" }).click();
  await expect.poll(() => retryCount).toBe(1);
  await expect(page.getByRole("button", { name: "View generation" })).toBeVisible();
  expect(createCount).toBe(1);
});
