import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const FAKE_AUDIO = new Uint8Array(1024).fill(0xff);

beforeAll(async () => {
	await env.R2_BUCKET.put(
		"books/franz-kafka/the-trial/audio/kokoro-af-heart/chapter-01.opus",
		FAKE_AUDIO,
	);
});

describe("GET /api/v1/books/:author/:title/audio/:chapter", () => {
	it("streams audio with correct content type", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/audio/01", {}, env);
		expect(res.status).toBe(200);
		expect(res.headers.get("Content-Type")).toBe("audio/ogg");
		expect(res.headers.get("Accept-Ranges")).toBe("bytes");
		expect(res.headers.get("Content-Disposition")).toBe("inline");
		await res.arrayBuffer(); // drain body to release R2 handle
	});

	it("returns full content length", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/audio/01", {}, env);
		expect(res.headers.get("Content-Length")).toBe("1024");
		await res.arrayBuffer();
	});

	it("returns 404 for missing chapter", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/audio/99", {}, env);
		expect(res.status).toBe(404);
		await res.arrayBuffer();
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/foo/audio/01", {}, env);
		expect(res.status).toBe(400);
		await res.arrayBuffer();
	});

	it("returns 400 for invalid chapter format", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/audio/abc", {}, env);
		expect(res.status).toBe(400);
		await res.arrayBuffer();
	});

	it("has immutable cache-control", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/audio/01", {}, env);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
		await res.arrayBuffer();
	});

	it("handles range requests with 206", async () => {
		const res = await app.request(
			"/api/v1/books/franz-kafka/the-trial/audio/01",
			{ headers: { Range: "bytes=0-511" } },
			env,
		);
		expect(res.status).toBe(206);
		expect(res.headers.get("Content-Range")).toMatch(/^bytes 0-511\/1024$/);
		expect(res.headers.get("Content-Length")).toBe("512");
		await res.arrayBuffer();
	});

	it("handles range request without end", async () => {
		const res = await app.request(
			"/api/v1/books/franz-kafka/the-trial/audio/01",
			{ headers: { Range: "bytes=512-" } },
			env,
		);
		expect(res.status).toBe(206);
		await res.arrayBuffer();
	});

	it("returns 416 for invalid range header", async () => {
		const res = await app.request(
			"/api/v1/books/franz-kafka/the-trial/audio/01",
			{ headers: { Range: "invalid" } },
			env,
		);
		expect(res.status).toBe(416);
		await res.arrayBuffer();
	});

	it("zero-pads single digit chapter", async () => {
		const res = await app.request("/api/v1/books/franz-kafka/the-trial/audio/1", {}, env);
		expect(res.status).toBe(200);
		await res.arrayBuffer();
	});
});
