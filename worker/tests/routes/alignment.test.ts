import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const ALIGNMENT = {
	version: 1,
	rendition: "kokoro-af-heart",
	chapters: [
		{
			chapter: 1,
			words: [
				{ word: "Someone", start: 0.0, end: 0.35, chunk_idx: 0, element_id: "os-p-001" },
				{ word: "must", start: 0.36, end: 0.55, chunk_idx: 0, element_id: "os-p-001" },
			],
		},
	],
};

beforeAll(async () => {
	await env.R2_BUCKET.put(
		"books/franz-kafka/the-trial/audio/kokoro-af-heart/word_alignment.json",
		JSON.stringify(ALIGNMENT),
	);
});

describe("GET /api/v1/books/:author/:title/alignment", () => {
	it("returns alignment data", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/alignment", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<typeof ALIGNMENT>();
		expect(body.version).toBe(1);
		expect(body.chapters).toHaveLength(1);
		expect(body.chapters[0].words).toHaveLength(2);
	});

	it("has immutable cache-control", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/alignment", {}, env);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
	});

	it("returns 404 for missing alignment", async () => {
		const res = await app.request("/api/v1/books/nobody/nothing/alignment", {}, env);
		expect(res.status).toBe(404);
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/foo/alignment", {}, env);
		expect(res.status).toBe(400);
	});
});
