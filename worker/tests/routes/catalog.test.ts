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
			total_duration_seconds: 77400.5,
			chapter_count: 42,
			has_cover: true,
		},
	],
};

type CatalogResponse = typeof CATALOG & {
	total: number;
	page: number;
	limit: number;
};

beforeEach(async () => {
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

	it("returns empty catalog when catalog.json does not exist", async () => {
		await env.R2_BUCKET.delete("catalog.json");
		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(0);
		expect(body.total).toBe(0);
	});
});
