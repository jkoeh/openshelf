import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const AUTHOR = "franz-kafka";
const TITLE = "the-trial";
const RENDITION = "kokoro-af-heart";
const BUILD = "2a4f9c1b3d8e7f60";

const BOOK_MANIFEST = {
	title: "The Trial",
	author: "Franz Kafka",
	source: "gutenberg",
	renditions: {
		[RENDITION]: {
			voice: "af_heart",
			engine: "kokoro",
			display: "Heart",
			current_build: BUILD,
			available_builds: [BUILD],
		},
	},
};

const RENDITION_MANIFEST = {
	version: 2,
	build: BUILD,
	rendition: RENDITION,
	voice: "af_heart",
	engine: "kokoro",
	pipeline_version: "2",
	total_duration_seconds: 2880,
	section_count: 1,
	sections: [
		{
			sequence: 1,
			section_type: "chapter",
			ordinal: 1,
			display_label: "I",
			display_title: "The Arrest",
			filename: "section-01.m4a",
			duration_seconds: 2880,
			word_count: 5200,
		},
	],
};

beforeAll(async () => {
	await env.R2_BUCKET.put(`books/${AUTHOR}/${TITLE}/manifest.json`, JSON.stringify(BOOK_MANIFEST));
	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${TITLE}/audio/${RENDITION}/builds/${BUILD}/rendition-manifest.json`,
		JSON.stringify(RENDITION_MANIFEST),
	);
});

describe("GET /api/v1/books/:author/:title", () => {
	it("returns book manifest merged with current rendition manifest", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}`, {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<{
			title: string;
			author: string;
			renditions: Record<string, { current_build: string; sections: unknown[] }>;
		}>();
		expect(body.title).toBe("The Trial");
		expect(body.author).toBe("Franz Kafka");
		expect(body.renditions[RENDITION].current_build).toBe(BUILD);
		expect(body.renditions[RENDITION].sections).toHaveLength(1);
	});

	it("returns 404 for unknown book", async () => {
		const res = await app.request("/api/v1/books/nobody/nothing", {}, env);
		expect(res.status).toBe(404);
		const body = await res.json<{ error: { code: string } }>();
		expect(body.error.code).toBe("NOT_FOUND");
	});

	it("returns 404 when the current build manifest is missing", async () => {
		await env.R2_BUCKET.put(
			"books/franz-kafka/missing-build/manifest.json",
			JSON.stringify(BOOK_MANIFEST),
		);
		const res = await app.request("/api/v1/books/franz-kafka/missing-build", {}, env);
		expect(res.status).toBe(404);
	});

	it("returns 404 instead of normalizing a version-1 build", async () => {
		const legacyTitle = "legacy-build";
		await env.R2_BUCKET.put(
			`books/${AUTHOR}/${legacyTitle}/manifest.json`,
			JSON.stringify(BOOK_MANIFEST),
		);
		await env.R2_BUCKET.put(
			`books/${AUTHOR}/${legacyTitle}/audio/${RENDITION}/builds/${BUILD}/rendition-manifest.json`,
			JSON.stringify({ version: 1, chapters: [] }),
		);

		const res = await app.request(`/api/v1/books/${AUTHOR}/${legacyTitle}`, {}, env);

		expect(res.status).toBe(404);
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/The-Trial", {}, env);
		expect(res.status).toBe(400);
		const body = await res.json<{ error: { code: string } }>();
		expect(body.error.code).toBe("INVALID_PARAM");
	});

	it("has cache-control header", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}`, {}, env);
		expect(res.headers.get("Cache-Control")).toBe("public, max-age=60");
	});
});
