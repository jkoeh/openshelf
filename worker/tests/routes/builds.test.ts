import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import app from "../../src/index";

const AUTHOR = "franz-kafka";
const TITLE = "the-trial-builds";
const MISSING_TITLE = "missing-selection-build";
const RENDITION = "kokoro-af-heart";
const CURRENT_BUILD = "2a4f9c1b3d8e7f60";
const OLD_BUILD = "7e8b4d2a9c0e1234";

const BOOK_MANIFEST = {
	title: "The Trial",
	author: "Franz Kafka",
	source: "gutenberg",
	renditions: {
		[RENDITION]: {
			voice: "af_heart",
			engine: "kokoro",
			display: "Heart",
			current_build: CURRENT_BUILD,
			available_builds: [CURRENT_BUILD, OLD_BUILD],
		},
	},
};

function renditionManifest(build: string, totalDuration: number, pipelineVersion: string) {
	return {
		build,
		rendition: RENDITION,
		voice: "af_heart",
		engine: "kokoro",
		pipeline_version: pipelineVersion,
		total_duration_seconds: totalDuration,
		chapters: [
			{
				number: 1,
				title: "Chapter 1",
				filename: "chapter-01.m4a",
				duration_seconds: totalDuration,
				word_count: 5200,
			},
		],
	};
}

beforeAll(async () => {
	await env.R2_BUCKET.put(`books/${AUTHOR}/${TITLE}/manifest.json`, JSON.stringify(BOOK_MANIFEST));
	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${TITLE}/audio/${RENDITION}/builds/${CURRENT_BUILD}/rendition-manifest.json`,
		JSON.stringify(renditionManifest(CURRENT_BUILD, 2880, "2")),
	);
	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${TITLE}/audio/${RENDITION}/builds/${OLD_BUILD}/rendition-manifest.json`,
		JSON.stringify(renditionManifest(OLD_BUILD, 2875, "1")),
	);

	await env.R2_BUCKET.put(
		`books/${AUTHOR}/${MISSING_TITLE}/manifest.json`,
		JSON.stringify({
			...BOOK_MANIFEST,
			renditions: {
				[RENDITION]: {
					...BOOK_MANIFEST.renditions[RENDITION],
					available_builds: [CURRENT_BUILD],
				},
			},
		}),
	);
});

describe("GET /api/v1/books/:author/:title/builds", () => {
	it("returns retained builds enriched from rendition manifests", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/builds`, {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<{
			title: string;
			author: string;
			renditions: Record<
				string,
				{
					current_build: string;
					builds: {
						build: string;
						is_current: boolean;
						pipeline_version: string;
						total_duration_seconds: number;
						chapter_count: number;
						chapters: unknown[];
					}[];
				}
			>;
		}>();

		const rendition = body.renditions[RENDITION];
		expect(body.title).toBe("The Trial");
		expect(body.author).toBe("Franz Kafka");
		expect(rendition.current_build).toBe(CURRENT_BUILD);
		expect(rendition.builds).toHaveLength(2);
		expect(rendition.builds[0]).toMatchObject({
			build: CURRENT_BUILD,
			is_current: true,
			pipeline_version: "2",
			total_duration_seconds: 2880,
			chapter_count: 1,
		});
		expect(rendition.builds[1]).toMatchObject({
			build: OLD_BUILD,
			is_current: false,
			pipeline_version: "1",
			total_duration_seconds: 2875,
			chapter_count: 1,
		});
		expect(rendition.builds[0].chapters).toHaveLength(1);
	});

	it("is not cached", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${TITLE}/builds`, {}, env);
		expect(res.headers.get("Cache-Control")).toBe("no-store");
	});

	it("returns 404 for unknown book", async () => {
		const res = await app.request("/api/v1/books/nobody/nothing/builds", {}, env);
		expect(res.status).toBe(404);
		const body = await res.json<{ error: { code: string } }>();
		expect(body.error.code).toBe("NOT_FOUND");
	});

	it("returns 404 when a retained build manifest is missing", async () => {
		const res = await app.request(`/api/v1/books/${AUTHOR}/${MISSING_TITLE}/builds`, {}, env);
		expect(res.status).toBe(404);
		const body = await res.json<{ error: { code: string } }>();
		expect(body.error.code).toBe("NOT_FOUND");
	});

	it("returns 400 for invalid slug", async () => {
		const res = await app.request("/api/v1/books/INVALID/the-trial/builds", {}, env);
		expect(res.status).toBe(400);
		const body = await res.json<{ error: { code: string } }>();
		expect(body.error.code).toBe("INVALID_PARAM");
	});
});
