import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";

const CATALOG = {
	version: 1,
	generated_at: "2025-01-15T12:00:00Z",
	books: [
		{
			author: "Franz Kafka",
			author_slug: "franz-kafka",
			title: "The Trial",
			title_slug: "the-trial",
			source: "gutenberg",
			rendition: "kokoro-af-heart",
			current_build: "2a4f9c1b3d8e7f60",
			total_duration_seconds: 28800,
			chapter_count: 10,
			has_cover: false,
		},
		{
			author: "Fyodor Dostoevsky",
			author_slug: "fyodor-dostoevsky",
			title: "Crime and Punishment",
			title_slug: "crime-and-punishment",
			source: "gutenberg",
			rendition: "kokoro-af-heart",
			current_build: "7e8b4d2a9c0e1234",
			total_duration_seconds: 77400.5,
			chapter_count: 42,
			has_cover: true,
		},
	],
};

const FALLBACK_AUTHOR = "kafka-franz";
const FALLBACK_TITLE = "metamorphosis";
const FALLBACK_RENDITION = "kokoro-af-heart";
const FALLBACK_BUILD = "cd7919d6cfc5f7a1";
const FALLBACK_BOOK_MANIFEST_KEY = `books/${FALLBACK_AUTHOR}/${FALLBACK_TITLE}/manifest.json`;
const FALLBACK_RENDITION_MANIFEST_KEY =
	`books/${FALLBACK_AUTHOR}/${FALLBACK_TITLE}/audio/${FALLBACK_RENDITION}` +
	`/builds/${FALLBACK_BUILD}/rendition-manifest.json`;
const FALLBACK_COVER_KEY = `books/${FALLBACK_AUTHOR}/${FALLBACK_TITLE}/cover.png`;

const FALLBACK_BOOK_MANIFEST = {
	title: "Metamorphosis",
	author: "Franz Kafka",
	source: "gutenberg",
	renditions: {
		[FALLBACK_RENDITION]: {
			voice: "af_heart",
			engine: "kokoro",
			display: "Heart",
			current_build: FALLBACK_BUILD,
			available_builds: [FALLBACK_BUILD],
		},
	},
};

const FALLBACK_RENDITION_MANIFEST = {
	build: FALLBACK_BUILD,
	rendition: FALLBACK_RENDITION,
	voice: "af_heart",
	engine: "kokoro",
	pipeline_version: "1",
	total_duration_seconds: 6825.25,
	chapters: [
		{ number: 1, title: "I", filename: "chapter-01.m4a", duration_seconds: 2140.575 },
		{ number: 2, title: "II", filename: "chapter-02.m4a", duration_seconds: 2274.05 },
		{ number: 3, title: "III", filename: "chapter-03.m4a", duration_seconds: 2282.375 },
	],
};

type CatalogResponse = typeof CATALOG & {
	total: number;
	page: number;
	limit: number;
};

beforeEach(async () => {
	await env.R2_BUCKET.delete([
		FALLBACK_BOOK_MANIFEST_KEY,
		FALLBACK_RENDITION_MANIFEST_KEY,
		FALLBACK_COVER_KEY,
	]);
	await env.R2_BUCKET.put("catalog.json", JSON.stringify(CATALOG));
});

describe("GET /api/v1/catalog", () => {
	it("returns all books from catalog.json", async () => {
		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.version).toBe(1);
		expect(body.generated_at).toBe("2025-01-15T12:00:00Z");
		expect(body.books).toHaveLength(2);
		expect(body.total).toBe(2);
		expect(body.page).toBe(1);
	});

	it("filters by q param", async () => {
		const res = await app.request("/api/v1/catalog?q=kafka", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(1);
		expect(body.books[0].author_slug).toBe("franz-kafka");
	});

	it("filters by author param", async () => {
		const res = await app.request("/api/v1/catalog?author=dostoevsky", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(1);
		expect(body.books[0].title_slug).toBe("crime-and-punishment");
	});

	it("paginates results", async () => {
		const res = await app.request("/api/v1/catalog?page=2&limit=1", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(1);
		expect(body.page).toBe(2);
		expect(body.limit).toBe(1);
		expect(body.total).toBe(2);
	});

	it("returns empty when no match", async () => {
		const res = await app.request("/api/v1/catalog?q=nonexistent", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(0);
		expect(body.total).toBe(0);
	});

	it("derives catalog from book manifests when catalog.json does not exist", async () => {
		await env.R2_BUCKET.delete("catalog.json");
		await env.R2_BUCKET.put(FALLBACK_BOOK_MANIFEST_KEY, JSON.stringify(FALLBACK_BOOK_MANIFEST));
		await env.R2_BUCKET.put(
			FALLBACK_RENDITION_MANIFEST_KEY,
			JSON.stringify(FALLBACK_RENDITION_MANIFEST),
		);
		await env.R2_BUCKET.put(FALLBACK_COVER_KEY, "fake image");

		const res = await app.request("/api/v1/catalog?page=1&limit=20", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toEqual([
			expect.objectContaining({
				author: "Franz Kafka",
				author_slug: FALLBACK_AUTHOR,
				title: "Metamorphosis",
				title_slug: FALLBACK_TITLE,
				rendition: FALLBACK_RENDITION,
				current_build: FALLBACK_BUILD,
				total_duration_seconds: 6825.25,
				chapter_count: 3,
				has_cover: true,
			}),
		]);
		expect(body.total).toBe(1);
		expect(body.page).toBe(1);
		expect(body.limit).toBe(20);
	});

	it("returns empty catalog when catalog.json and book manifests do not exist", async () => {
		await env.R2_BUCKET.delete("catalog.json");
		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(0);
		expect(body.total).toBe(0);
	});
});
