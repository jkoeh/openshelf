import { env } from "cloudflare:test";
import { describe, it, expect, beforeAll } from "vitest";
import app from "../../src/index";

const FAKE_EPUB = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);

beforeAll(async () => {
	await env.R2_BUCKET.put("books/franz-kafka/the-trial/book.epub", FAKE_EPUB);
});

describe("GET /api/v1/books/:author/:title/epub", () => {
	it("returns epub with correct content type", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/epub", {}, env);
		expect(res.status).toBe(200);
		expect(res.headers.get("Content-Type")).toBe("application/epub+zip");
		await res.arrayBuffer();
	});

	it("has attachment content disposition", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/epub", {}, env);
		expect(res.headers.get("Content-Disposition")).toContain("the-trial.epub");
		await res.arrayBuffer();
	});

	it("has immutable cache-control", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/epub", {}, env);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
		await res.arrayBuffer();
	});

	it("returns 404 for missing epub", async () => {
		const res = await app.request("/api/v1/books/nobody/nothing/epub", {}, env);
		expect(res.status).toBe(404);
		await res.arrayBuffer();
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/foo/epub", {}, env);
		expect(res.status).toBe(400);
		await res.arrayBuffer();
	});
});
