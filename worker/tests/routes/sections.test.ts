import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const AUTHOR = "lewis-carroll";
const TITLE = "alices-adventures-in-wonderland";
const RENDITION = "kokoro-af-heart";
const BUILD = "2a4f9c1b3d8e7f60";
const QUERY = `rendition=${RENDITION}&build=${BUILD}`;

const SECTION_DATA = {
	version: 2,
	rendition: RENDITION,
	build: BUILD,
	sections: [
		{
			sequence: 1,
			section_type: "epilogue",
			ordinal: null,
			heading: {
				display_label: "Epilogue",
				display_title: "",
				spoken_text: "Epilogue.",
				words: [{ word: "Epilogue", start: 0, end: 0.4 }],
			},
			chunks: [
				{
					text: "At last the story was done.",
					words: [
						{ word: "At", start: 1.2, end: 1.3 },
						{ word: "last", start: 1.31, end: 1.5 },
					],
				},
			],
			word_count: 6,
		},
		{
			sequence: 2,
			section_type: "chapter",
			ordinal: 1,
			heading: {
				display_label: "I",
				display_title: "Down the Rabbit-Hole",
				spoken_text: "Chapter One. Down the Rabbit-Hole.",
				words: [
					{ word: "Chapter", start: 0, end: 0.3 },
					{ word: "One", start: 0.31, end: 0.5 },
				],
			},
			chunks: [
				{
					text: "Alice was beginning to get very tired.",
					words: [{ word: "Alice", start: 1.3, end: 1.6 }],
				},
			],
			word_count: 8,
		},
	],
};

beforeAll(async () => {
	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${TITLE}/audio/${RENDITION}/builds/${BUILD}/section_data.json`,
		JSON.stringify(SECTION_DATA),
	);
});

describe("GET /api/v1/books/:author/:title/sections/:sequence", () => {
	it("returns a special section without assigning a chapter ordinal", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1?${QUERY}`,
			{},
			env,
		);
		expect(res.status).toBe(200);
		const body = await res.json<{
			sequence: number;
			section_type: string;
			ordinal: number | null;
			heading: { display_label: string; spoken_text: string };
			chunks: string[];
			words: Array<{ word: string; region: string; chunk_idx: number | null }>;
		}>();
		expect(body.sequence).toBe(1);
		expect(body.section_type).toBe("epilogue");
		expect(body.ordinal).toBeNull();
		expect(body.heading.display_label).toBe("Epilogue");
		expect(body.chunks).toEqual(["At last the story was done."]);
		expect(body.words[0]).toMatchObject({
			word: "Epilogue",
			region: "heading",
			chunk_idx: null,
		});
		expect(body.words[1]).toMatchObject({ word: "At", region: "body", chunk_idx: 0 });
	});

	it("keeps display and spoken chapter headings distinct", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/2?${QUERY}`,
			{},
			env,
		);
		const body = await res.json<{
			ordinal: number;
			heading: { display_label: string; spoken_text: string };
			chunks: string[];
		}>();
		expect(body.ordinal).toBe(1);
		expect(body.heading.display_label).toBe("I");
		expect(body.heading.spoken_text).toContain("Chapter One");
		expect(body.chunks[0]).toMatch(/^Alice was beginning/);
	});

	it("requires rendition and build query params", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1`,
			{},
			env,
		);
		expect(res.status).toBe(400);
	});

	it("returns 404 for a missing section or build", async () => {
		const missingSection = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/99?${QUERY}`,
			{},
			env,
		);
		expect(missingSection.status).toBe(404);

		const missingBuild = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1?rendition=${RENDITION}&build=ffffffffffffffff`,
			{},
			env,
		);
		expect(missingBuild.status).toBe(404);
	});

	it("validates sequence and slug values", async () => {
		const invalidSequence = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/abc?${QUERY}`,
			{},
			env,
		);
		expect(invalidSequence.status).toBe(400);

		const invalidSlug = await app.request(
			`/api/v1/books/INVALID/foo/sections/1?${QUERY}`,
			{},
			env,
		);
		expect(invalidSlug.status).toBe(400);
	});

	it("has immutable cache-control", async () => {
		const res = await app.request(
			`/api/v1/books/${AUTHOR}/${TITLE}/sections/1?${QUERY}`,
			{},
			env,
		);
		expect(res.headers.get("Cache-Control")).toContain("immutable");
	});
});
