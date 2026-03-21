import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const MANIFEST = {
	title: "The Trial",
	author: "Franz Kafka",
	source: "gutenberg",
	rendition: "kokoro-af-heart",
	chapters: [
		{
			number: 1,
			title: "Chapter 1",
			filename: "chapter-01.opus",
			duration_seconds: 2880,
			word_count: 5200,
		},
	],
};

beforeAll(async () => {
	await env.R2_BUCKET.put(
		"books/franz-kafka/the-trial/audio/kokoro-af-heart/manifest.json",
		JSON.stringify(MANIFEST),
	);
});

describe("GET /api/v1/books/:author/:title", () => {
	it("returns manifest", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(body.title).toBe("The Trial");
		expect(body.author).toBe("Franz Kafka");
	});

	it("returns 404 for unknown book", async () => {
		const res = await app.request("/api/v1/books/nobody/nothing", {}, env);
		expect(res.status).toBe(404);
		const body = await res.json();
		expect(body.error.code).toBe("NOT_FOUND");
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/The-Trial", {}, env);
		expect(res.status).toBe(400);
		const body = await res.json();
		expect(body.error.code).toBe("INVALID_PARAM");
	});

	it("has cache-control header", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial", {}, env);
		expect(res.headers.get("Cache-Control")).toBe("public, max-age=60");
	});
});
