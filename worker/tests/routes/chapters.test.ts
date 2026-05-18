import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const AUTHOR = "franz-kafka";
const TITLE = "the-trial";
const RENDITION = "kokoro-af-heart";
const BUILD = "2a4f9c1b3d8e7f60";
const QUERY = `rendition=${RENDITION}&build=${BUILD}`;

const CHAPTER_DATA = {
	version: 1,
	rendition: RENDITION,
	build: BUILD,
	chapters: [
		{
			number: 1,
			title: "Chapter 1",
			chunks: [
				{
					text: "Someone must have been telling lies about Josef K.",
					words: [
						{ word: "Someone", start: 0.0, end: 0.3 },
						{ word: "must", start: 0.3, end: 0.5 },
					],
				},
				{
					text: "Every day at eight in the morning he was brought his breakfast.",
					words: [
						{ word: "Every", start: 3.0, end: 3.2 },
						{ word: "day", start: 3.2, end: 3.4 },
					],
				},
			],
			word_count: 20,
		},
		{
			number: 2,
			title: "Chapter 2",
			chunks: [
				{
					text: "K. was informed next day that his first interrogation would take place.",
					words: [{ word: "K", start: 0.0, end: 0.1 }],
				},
			],
			word_count: 12,
		},
	],
};

beforeAll(async () => {
	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${TITLE}/audio/${RENDITION}/builds/${BUILD}/chapter_data.json`,
		JSON.stringify(CHAPTER_DATA),
	);
});

describe("GET /api/v1/books/:author/:title/chapters/:number", () => {
	it("returns chapter with inline words from a build-pinned key", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/chapters/1?${QUERY}`, {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<{
			number: number;
			title: string;
			chunks: string[];
			word_count: number;
			words: Array<{ word: string; start: number; end: number; chunk_idx: number }>;
		}>();
		expect(body.number).toBe(1);
		expect(body.title).toBe("Chapter 1");
		expect(body.chunks).toHaveLength(2);
		expect(body.chunks[0]).toBe("Someone must have been telling lies about Josef K.");
		expect(body.word_count).toBe(20);
		expect(body.words).toHaveLength(4);
		expect(body.words[0]).toEqual({ word: "Someone", start: 0.0, end: 0.3, chunk_idx: 0 });
		expect(body.words[1]).toEqual({ word: "must", start: 0.3, end: 0.5, chunk_idx: 0 });
		expect(body.words[2]).toEqual({ word: "Every", start: 3.0, end: 3.2, chunk_idx: 1 });
		expect(body.words[3]).toEqual({ word: "day", start: 3.2, end: 3.4, chunk_idx: 1 });
	});

	it("requires rendition and build query params", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/chapters/1`, {}, env);
		expect(res.status).toBe(400);
	});

	it("returns chapter 2 with words", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/chapters/2?${QUERY}`, {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<{
			number: number;
			words: Array<{ word: string; chunk_idx: number }>;
		}>();
		expect(body.number).toBe(2);
		expect(body.words).toHaveLength(1);
		expect(body.words[0].chunk_idx).toBe(0);
	});

	it("returns 404 for nonexistent chapter", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/chapters/99?${QUERY}`, {}, env);
		expect(res.status).toBe(404);
	});

	it("returns 404 for unknown build", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/chapters/1?rendition=${RENDITION}&build=ffffffffffffffff`,
			{},
			env,
		);
		expect(res.status).toBe(404);
	});

	it("returns 400 for invalid chapter number", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/chapters/abc?${QUERY}`,
			{},
			env,
		);
		expect(res.status).toBe(400);
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request(`/api/v1/books/INVALID/foo/chapters/1?${QUERY}`, {}, env);
		expect(res.status).toBe(400);
	});

	it("has immutable cache-control", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/chapters/1?${QUERY}`, {}, env);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
	});
});
