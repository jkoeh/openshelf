import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const AUTHOR = "franz-kafka";
const TITLE = "the-trial";
const RENDITION = "kokoro-af-heart";
const BUILD = "2a4f9c1b3d8e7f60";
const QUERY = `rendition=${RENDITION}&build=${BUILD}`;
const FAKE_AUDIO = new Uint8Array(1024).fill(0xff);

beforeAll(async () => {
	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${TITLE}/audio/${RENDITION}/builds/${BUILD}/section-01.m4a`,
		FAKE_AUDIO,
	);
});

describe("GET /api/v1/books/:author/:title/sections/:sequence/audio", () => {
	it("streams audio with correct content type", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`, {}, env);
		expect(res.status).toBe(200);
		expect(res.headers.get("Content-Type")).toBe("audio/mp4");
		expect(res.headers.get("Accept-Ranges")).toBe("bytes");
		expect(res.headers.get("Content-Disposition")).toBe("inline");
		await res.arrayBuffer();
	});

	it("requires rendition and build query params", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio`, {}, env);
		expect(res.status).toBe(400);
		await res.arrayBuffer();
	});

	it("returns full content length", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`, {}, env);
		expect(res.headers.get("Content-Length")).toBe("1024");
		await res.arrayBuffer();
	});

	it("returns 404 for missing section", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/99/audio?${QUERY}`, {}, env);
		expect(res.status).toBe(404);
		await res.arrayBuffer();
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request(`/api/v1/books/INVALID/foo/sections/1/audio?${QUERY}`, {}, env);
		expect(res.status).toBe(400);
		await res.arrayBuffer();
	});

	it("returns 400 for invalid section format", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/abc/audio?${QUERY}`, {}, env);
		expect(res.status).toBe(400);
		await res.arrayBuffer();
	});

	it("has immutable cache-control", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`, {}, env);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
		await res.arrayBuffer();
	});

	it("handles range requests with 206", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`,
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
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`,
			{ headers: { Range: "bytes=512-" } },
			env,
		);
		expect(res.status).toBe(206);
		await res.arrayBuffer();
	});

	it("returns 416 for invalid range header", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`,
			{ headers: { Range: "invalid" } },
			env,
		);
		expect(res.status).toBe(416);
		await res.arrayBuffer();
	});

	it("maps a single-digit sequence to a zero-padded storage key", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/sections/1/audio?${QUERY}`, {}, env);
		expect(res.status).toBe(200);
		await res.arrayBuffer();
	});
});
