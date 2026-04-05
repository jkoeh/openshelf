import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

// Manifests seeded at the R2 key paths the catalog route scans
const KAFKA_MANIFEST = {
	title: "The Trial",
	author: "Franz Kafka",
	source: "gutenberg",
	rendition: "kokoro-af-heart",
	total_duration_seconds: 28800,
	chapters: Array.from({ length: 10 }, (_, i) => ({
		number: i + 1,
		title: `Chapter ${i + 1}`,
		filename: `chapter-${String(i + 1).padStart(2, "0")}.m4a`,
		duration_seconds: 2880,
		word_count: 5000,
	})),
};

const DOSTOEVSKY_MANIFEST = {
	title: "Crime and Punishment",
	author: "Fyodor Dostoevsky",
	source: "gutenberg",
	rendition: "kokoro-af-heart",
	total_duration_seconds: 77400.5,
	chapters: Array.from({ length: 42 }, (_, i) => ({
		number: i + 1,
		title: `Chapter ${i + 1}`,
		filename: `chapter-${String(i + 1).padStart(2, "0")}.m4a`,
		duration_seconds: 1842.87,
		word_count: 3000,
	})),
};

type CatalogResponse = {
	books: Array<{
		author: string;
		author_slug: string;
		title: string;
		title_slug: string;
		source: string;
		rendition: string;
		total_duration_seconds: number;
		chapter_count: number;
	}>;
	total: number;
	page: number;
	limit: number;
};

beforeAll(async () => {
	await env.R2_BUCKET.put(
		"books/franz-kafka/the-trial/audio/kokoro-af-heart/manifest.json",
		JSON.stringify(KAFKA_MANIFEST),
	);
	await env.R2_BUCKET.put(
		"books/fyodor-dostoevsky/crime-and-punishment/audio/kokoro-af-heart/manifest.json",
		JSON.stringify(DOSTOEVSKY_MANIFEST),
	);
});

describe("GET /api/v1/catalog", () => {
	it("returns all books", async () => {
		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
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

	it("returns empty catalog when no manifests exist", async () => {
		// Delete all manifests
		await env.R2_BUCKET.delete(
			"books/franz-kafka/the-trial/audio/kokoro-af-heart/manifest.json",
		);
		await env.R2_BUCKET.delete(
			"books/fyodor-dostoevsky/crime-and-punishment/audio/kokoro-af-heart/manifest.json",
		);

		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(0);
		expect(body.total).toBe(0);

		// Restore for other tests
		await env.R2_BUCKET.put(
			"books/franz-kafka/the-trial/audio/kokoro-af-heart/manifest.json",
			JSON.stringify(KAFKA_MANIFEST),
		);
		await env.R2_BUCKET.put(
			"books/fyodor-dostoevsky/crime-and-punishment/audio/kokoro-af-heart/manifest.json",
			JSON.stringify(DOSTOEVSKY_MANIFEST),
		);
	});
});
