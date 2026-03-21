import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const CHUNKS = {
	version: 1,
	chapters: [
		{
			number: 1,
			title: "Chapter 1",
			chunks: [
				"Someone must have been telling lies about Josef K.",
				"Every day at eight in the morning he was brought his breakfast.",
			],
		},
		{
			number: 2,
			title: "Chapter 2",
			chunks: ["K. was informed next day that his first interrogation would take place."],
		},
	],
};

beforeAll(async () => {
	await env.R2_BUCKET.put("books/franz-kafka/the-trial/chunks.json", JSON.stringify(CHUNKS));
});

describe("GET /api/v1/books/:author/:title/chapters/:number", () => {
	it("returns chapter text", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/chapters/1", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<{ number: number; title: string; chunks: string[]; word_count: number }>();
		expect(body.number).toBe(1);
		expect(body.title).toBe("Chapter 1");
		expect(body.chunks).toHaveLength(2);
		expect(body.word_count).toBeGreaterThan(0);
	});

	it("returns chapter 2", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/chapters/2", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<{ number: number }>();
		expect(body.number).toBe(2);
	});

	it("returns 404 for nonexistent chapter", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/chapters/99", {}, env);
		expect(res.status).toBe(404);
	});

	it("returns 400 for invalid chapter number", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/chapters/abc", {}, env);
		expect(res.status).toBe(400);
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/foo/chapters/1", {}, env);
		expect(res.status).toBe(400);
	});

	it("has immutable cache-control", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/chapters/1", {}, env);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
	});
});
