import { env } from "cloudflare:test";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";

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

describe("owner book management", () => {
	const root = "books/jane-austen/pride-g1342";
	const path = "/api/v1/books/jane-austen/pride-g1342";
	const ownerToken = "test-owner-token-longer-than-twenty-four";
	const limiter = { limit: async () => ({ success: true }) } as RateLimit;
	const bindings: Env = { ...env, OWNER_TOKEN: ownerToken, PC_TOKEN: "test-consumer-token-longer-than-twenty-four",
		SEARCH_RATE_LIMITER: limiter, CREATE_RATE_LIMITER: limiter, AUTH_RATE_LIMITER: limiter };
	const req = (suffix = "", method = "GET", token: string | null = ownerToken) => app.request(path + suffix, {
		method, headers: token ? { Authorization: `Bearer ${token}` } : {},
	}, bindings);
	beforeEach(async () => {
		await env.JOB_DB.exec("DELETE FROM book_deletions; DELETE FROM generation_jobs; DELETE FROM generation_starts; DELETE FROM source_tokens; DELETE FROM source_books;");
		await env.JOB_DB.prepare(`INSERT INTO source_books(source_id,title,author,epub_url,author_slug,title_slug,updated_at)
			VALUES ('gutenberg:1342','Pride and Prejudice','Jane Austen','https://www.gutenberg.org/ebooks/1342.epub3.images','jane-austen','pride-g1342','2026-10-11')`).run();
		await env.R2_BUCKET.put(`${root}/manifest.json`, JSON.stringify(BOOK_MANIFEST));
		await env.R2_BUCKET.put(`${root}/audio/kokoro-af-heart/builds/${BUILD}/section-01.m4a`, "audio");
		await env.R2_BUCKET.put(`${root}/audio/chatterbox-af-heart/builds/${BUILD}/section_data.json`, "{}");
		await env.R2_BUCKET.put(`${root}/book.epub`, "epub");
		await env.R2_BUCKET.put(`${root}/cover.jpg`, "cover");
		await env.R2_BUCKET.put(`${root}-other/manifest.json`, "neighbor");
		await env.R2_BUCKET.put("catalog.json", JSON.stringify({ version: 2, books: [
			{ author_slug: "jane-austen", title_slug: "pride-g1342" },
			{ author_slug: "other", title_slug: "keep" },
		] }));
	});
	async function job(state: string) {
		const id = crypto.randomUUID();
		await env.JOB_DB.prepare("INSERT INTO generation_starts VALUES (?,'2026-10-11','2026-10-11')").bind(id).run();
		await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at)
			VALUES (?,'gutenberg:1342',?,?,?,?,'2026-10-11','2026-10-11')`).bind(id, BUILD, id, state, state).run();
		return id;
	}
	it("rejects visitors, PC credentials and forged Google tokens before mutation", async () => {
		for (const token of [null, bindings.PC_TOKEN!, "forged.google.token"]) {
			expect((await req("/management", "GET", token)).status).toBe(401);
			expect((await req("", "DELETE", token)).status).toBe(401);
		}
		expect(await env.R2_BUCKET.head(`${root}/manifest.json`)).not.toBeNull();
	});
	it("resolves the exact source and active generation for owner controls", async () => {
		const id = await job("queued");
		const response = await req("/management");
		expect(await response.json()).toEqual({ source_id: "gutenberg:1342", active_job_id: id, deleting: false });
		expect(response.headers.get("Cache-Control")).toBe("no-store");
		expect((await req("", "DELETE")).status).toBe(409);
		expect(await env.R2_BUCKET.head(`${root}/manifest.json`)).not.toBeNull();
	});
	it("waits for recently canceled claimed work to stop before deleting", async () => {
		const id = await job("canceled");
		await env.JOB_DB.prepare("UPDATE generation_jobs SET attempts=1,updated_at=? WHERE id=?")
			.bind(new Date().toISOString(), id).run();
		expect((await req("", "DELETE")).status).toBe(409);
		expect(await env.R2_BUCKET.head(`${root}/manifest.json`)).not.toBeNull();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET updated_at=? WHERE id=?")
			.bind(new Date(Date.now() - 121_000).toISOString(), id).run();
		expect((await req("", "DELETE")).status).toBe(200);
	});
	it("deletes every rendition and catalog row, preserves neighbors and source reading, and retires jobs", async () => {
		const id = await job("completed");
		const response = await req("", "DELETE");
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ deleted: true });
		expect((await env.R2_BUCKET.list({ prefix: `${root}/` })).objects).toHaveLength(0);
		expect(await env.R2_BUCKET.head(`${root}-other/manifest.json`)).not.toBeNull();
		const catalog = await (await env.R2_BUCKET.get("catalog.json"))!.json<{ books: unknown[] }>();
		expect(catalog.books).toEqual([{ author_slug: "other", title_slug: "keep" }]);
		const source = await env.JOB_DB.prepare("SELECT author_slug,title_slug,epub_url FROM source_books WHERE source_id='gutenberg:1342'").first();
		expect(source).toMatchObject({ author_slug: null, title_slug: null, epub_url: "https://www.gutenberg.org/ebooks/1342.epub3.images" });
		expect(await env.JOB_DB.prepare("SELECT state,stage,attempts FROM generation_jobs WHERE id=?").bind(id).first())
			.toEqual({ state: "canceled", stage: "deleted", attempts: 3 });
		expect((await req("", "DELETE")).status).toBe(200);
	});
	it("keeps a guard after interruption, rejects generation, and allows owner cleanup retry", async () => {
		await env.R2_BUCKET.put("catalog.json", '{"books":null}');
		expect((await req("", "DELETE")).status).toBe(503);
		expect(await (await req("/management")).json()).toMatchObject({ deleting: true });
		const create = await app.request("/api/v1/generation-jobs", { method: "POST", headers: {
			Authorization: `Bearer ${ownerToken}`, "Content-Type": "application/json",
		}, body: JSON.stringify({ source_id: "gutenberg:1342", regenerate: true }) }, bindings);
		expect(create.status).toBe(409);
		await expect(job("queued")).rejects.toThrow("BOOK_DELETING");
		await env.R2_BUCKET.put("catalog.json", '{"version":2,"books":[]}');
		expect((await req("", "DELETE")).status).toBe(200);
		expect(await env.JOB_DB.prepare("SELECT 1 FROM book_deletions").first()).toBeNull();
	});
	it("deletes paginated objects and preserves a concurrent catalog publication", async () => {
		let raced = false;
		const bucket = new Proxy(bindings.R2_BUCKET, {
			get(target, property) {
				if (property === "list") return (options: R2ListOptions) => target.list({ ...options, limit: 2 });
				if (property === "put") return async (...args: Parameters<R2Bucket["put"]>) => {
					if (args[0] === "catalog.json" && args[2]?.onlyIf && !raced) {
						raced = true;
						await target.put("catalog.json", JSON.stringify({ version: 2, books: [
							{ author_slug: "jane-austen", title_slug: "pride-g1342" },
							{ author_slug: "other", title_slug: "new-book" },
						] }));
					}
					return target.put(...args);
				};
				const value = Reflect.get(target, property);
				return typeof value === "function" ? value.bind(target) : value;
			},
		});
		const response = await app.request(path, { method: "DELETE", headers: { Authorization: `Bearer ${ownerToken}` } },
			{ ...bindings, R2_BUCKET: bucket });
		expect(response.status).toBe(200);
		expect(raced).toBe(true);
		expect((await env.R2_BUCKET.list({ prefix: `${root}/` })).objects).toHaveLength(0);
		expect(await (await env.R2_BUCKET.get("catalog.json"))!.json()).toMatchObject({
			books: [{ author_slug: "other", title_slug: "new-book" }],
		});
	});
	it("rejects overlapping deletions and supports manual books without an indexed source", async () => {
		await env.JOB_DB.prepare("UPDATE source_books SET author_slug=NULL,title_slug=NULL").run();
		expect(await (await req("/management")).json()).toMatchObject({ source_id: null });
		await env.JOB_DB.prepare("INSERT INTO book_deletions VALUES ('jane-austen','pride-g1342','other',?)")
			.bind(new Date(Date.now() + 600_000).toISOString()).run();
		expect((await req("", "DELETE")).status).toBe(409);
		expect(await env.R2_BUCKET.head(`${root}/manifest.json`)).not.toBeNull();
	});
	it("publishes owner routes in OpenAPI and allows browser DELETE preflight", async () => {
		const spec = await (await app.request("/api/v1/openapi.json", {}, bindings)).json<{ paths: Record<string, Record<string, unknown>> }>();
		expect(spec.paths["/api/v1/books/{author}/{title}"].delete).toBeDefined();
		expect(spec.paths["/api/v1/books/{author}/{title}/management"].get).toBeDefined();
		const preflight = await req("", "OPTIONS", null);
		expect(preflight.headers.get("Access-Control-Allow-Methods")).toContain("DELETE");
	});
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
