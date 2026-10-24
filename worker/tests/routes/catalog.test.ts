import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";

const pcToken = "catalog-test-pc-token-longer-than-twenty-four";
const limiter = { limit: async () => ({ success: true }) } as RateLimit;
const bindings: Env = {
	...env,
	PC_TOKEN: pcToken,
	AUTH_RATE_LIMITER: limiter,
	SEARCH_RATE_LIMITER: limiter,
	CREATE_RATE_LIMITER: limiter,
};
const sync = (books: { author_slug: string; title_slug: string }[], token = pcToken) =>
	app.request(
		"/api/v1/internal/catalog/sync",
		{
			method: "POST",
			headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
			body: JSON.stringify({ books }),
		},
		bindings,
	);
const reconcile = (
	after?: { author_slug: string; title_slug: string },
	useBindings = bindings,
	token = pcToken,
) =>
	app.request(
		"/api/v1/internal/catalog/reconcile",
		{
			method: "POST",
			headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
			body: JSON.stringify(after ? { after } : {}),
		},
		useBindings,
	);

const CATALOG = {
	version: 2,
	generated_at: "2025-01-15T12:00:00Z",
	books: [
		{
			author: "Franz Kafka",
			author_slug: "franz-kafka",
			title: "The Trial",
			title_slug: "the-trial",
			source: "gutenberg",
			rendition: "kokoro-af-heart",
			current_build: "2a4f9c1b3d8e7f60",
			total_duration_seconds: 28800,
			section_count: 10,
			has_cover: false,
		},
		{
			author: "Fyodor Dostoevsky",
			author_slug: "fyodor-dostoevsky",
			title: "Crime and Punishment",
			title_slug: "crime-and-punishment",
			source: "gutenberg",
			rendition: "kokoro-af-heart",
			current_build: "7e8b4d2a9c0e1234",
			total_duration_seconds: 77400.5,
			section_count: 42,
			has_cover: true,
		},
	],
};

const FALLBACK_AUTHOR = "kafka-franz";
const FALLBACK_TITLE = "metamorphosis";
const FALLBACK_RENDITION = "kokoro-af-heart";
const FALLBACK_BUILD = "cd7919d6cfc5f7a1";
const FALLBACK_BOOK_MANIFEST_KEY = `books/${FALLBACK_AUTHOR}/${FALLBACK_TITLE}/manifest.json`;
const FALLBACK_RENDITION_MANIFEST_KEY =
	`books/${FALLBACK_AUTHOR}/${FALLBACK_TITLE}/audio/${FALLBACK_RENDITION}` +
	`/builds/${FALLBACK_BUILD}/rendition-manifest.json`;
const FALLBACK_COVER_KEY = `books/${FALLBACK_AUTHOR}/${FALLBACK_TITLE}/cover.png`;

const FALLBACK_BOOK_MANIFEST = {
	title: "Metamorphosis",
	author: "Franz Kafka",
	source: "gutenberg",
	renditions: {
		[FALLBACK_RENDITION]: {
			voice: "af_heart",
			engine: "kokoro",
			display: "Heart",
			current_build: FALLBACK_BUILD,
			available_builds: [FALLBACK_BUILD],
		},
	},
};

const FALLBACK_RENDITION_MANIFEST = {
	version: 2,
	build: FALLBACK_BUILD,
	rendition: FALLBACK_RENDITION,
	voice: "af_heart",
	engine: "kokoro",
	pipeline_version: "2",
	total_duration_seconds: 6825.25,
	section_count: 3,
	sections: [
		{
			sequence: 1,
			section_type: "chapter",
			ordinal: 1,
			display_label: "I",
			display_title: "",
			filename: "section-01.m4a",
			duration_seconds: 2140.575,
			word_count: 100,
		},
		{
			sequence: 2,
			section_type: "chapter",
			ordinal: 2,
			display_label: "II",
			display_title: "",
			filename: "section-02.m4a",
			duration_seconds: 2274.05,
			word_count: 100,
		},
		{
			sequence: 3,
			section_type: "epilogue",
			ordinal: null,
			display_label: "Epilogue",
			display_title: "",
			filename: "section-03.m4a",
			duration_seconds: 2282.375,
			word_count: 100,
		},
	],
};

type CatalogResponse = typeof CATALOG & {
	total: number;
	page: number;
	limit: number;
};

beforeEach(async () => {
	await env.JOB_DB.exec("DELETE FROM published_books; DELETE FROM book_deletions;");
	await env.R2_BUCKET.delete([
		FALLBACK_BOOK_MANIFEST_KEY,
		FALLBACK_RENDITION_MANIFEST_KEY,
		FALLBACK_COVER_KEY,
	]);
	await env.R2_BUCKET.put("catalog.json", JSON.stringify(CATALOG));
	for (const book of CATALOG.books) {
		const root = `books/${book.author_slug}/${book.title_slug}`;
		await env.R2_BUCKET.put(
			`${root}/manifest.json`,
			JSON.stringify({
				title: book.title,
				author: book.author,
				source: book.source,
				renditions: { [book.rendition]: { current_build: book.current_build } },
			}),
		);
		await env.R2_BUCKET.put(
			`${root}/audio/${book.rendition}/builds/${book.current_build}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build: book.current_build,
				total_duration_seconds: book.total_duration_seconds,
				sections: Array.from({ length: book.section_count }, (_, i) => ({ sequence: i + 1 })),
			}),
		);
		if (book.has_cover) await env.R2_BUCKET.put(`${root}/cover.jpg`, "cover");
	}
	expect((await sync(CATALOG.books)).status).toBe(200);
});

describe("GET /api/v1/catalog", () => {
	it("returns current D1 publications independently of a stale R2 catalog artifact", async () => {
		await env.R2_BUCKET.put("catalog.json", JSON.stringify({ version: 2, books: [] }));
		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.version).toBe(2);
		expect(Number.isNaN(Date.parse(body.generated_at))).toBe(false);
		expect(body.books).toHaveLength(2);
		expect(body.total).toBe(2);
		expect(body.page).toBe(1);
		expect(res.headers.get("Cache-Control")).toBe("no-store");
	});

	it("filters by q param", async () => {
		const res = await app.request("/api/v1/catalog?q=kafka", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(1);
		expect(body.books[0].author_slug).toBe("franz-kafka");
	});

	it("filters by author param", async () => {
		const res = await app.request("/api/v1/catalog?author=dostoevsky", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(1);
		expect(body.books[0].title_slug).toBe("crime-and-punishment");
	});

	it("paginates results", async () => {
		const res = await app.request("/api/v1/catalog?page=2&limit=1", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(1);
		expect(body.page).toBe(2);
		expect(body.limit).toBe(1);
		expect(body.total).toBe(2);
	});

	it("returns empty when no match", async () => {
		const res = await app.request("/api/v1/catalog?q=nonexistent", {}, env);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(0);
		expect(body.total).toBe(0);
	});

	it("backfills manually published books from actual manifests through authenticated sync", async () => {
		await env.JOB_DB.exec("DELETE FROM published_books;");
		await env.R2_BUCKET.delete("catalog.json");
		await env.R2_BUCKET.put(FALLBACK_BOOK_MANIFEST_KEY, JSON.stringify(FALLBACK_BOOK_MANIFEST));
		await env.R2_BUCKET.put(
			FALLBACK_RENDITION_MANIFEST_KEY,
			JSON.stringify(FALLBACK_RENDITION_MANIFEST),
		);
		await env.R2_BUCKET.put(FALLBACK_COVER_KEY, "fake image");
		expect(
			(await sync([{ author_slug: FALLBACK_AUTHOR, title_slug: FALLBACK_TITLE }], "wrong-token"))
				.status,
		).toBe(401);
		expect(
			(await sync([{ author_slug: FALLBACK_AUTHOR, title_slug: FALLBACK_TITLE }])).status,
		).toBe(200);

		const res = await app.request("/api/v1/catalog?page=1&limit=20", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.version).toBe(2);
		expect(body.books).toEqual([
			expect.objectContaining({
				author: "Franz Kafka",
				author_slug: FALLBACK_AUTHOR,
				title: "Metamorphosis",
				title_slug: FALLBACK_TITLE,
				rendition: FALLBACK_RENDITION,
				current_build: FALLBACK_BUILD,
				total_duration_seconds: 6825.25,
				section_count: 3,
				has_cover: true,
			}),
		]);
		expect(body.total).toBe(1);
		expect(body.page).toBe(1);
		expect(body.limit).toBe(20);
	});

	it("returns an empty D1 catalog without falling back to stale R2 objects", async () => {
		await env.JOB_DB.exec("DELETE FROM published_books;");
		await env.R2_BUCKET.delete("catalog.json");
		const res = await app.request("/api/v1/catalog", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json<CatalogResponse>();
		expect(body.books).toHaveLength(0);
		expect(body.total).toBe(0);
	});
	it("matches Chinese scripts and updates the same query immediately after publication and deletion", async () => {
		const book = {
			author_slug: "cao-xueqin",
			title_slug: "hongloumeng",
			title: "紅樓夢",
			author: "曹雪芹",
			source: "local",
		};
		const root = `books/${book.author_slug}/${book.title_slug}`;
		const build = "1234567890abcdef";
		const query = "/api/v1/catalog?q=" + encodeURIComponent("红楼梦");
		const search = async () => await (await app.request(query, {}, env)).json<CatalogResponse>();
		expect((await search()).books).toEqual([]);
		await env.R2_BUCKET.put(
			`${root}/manifest.json`,
			JSON.stringify({ ...book, renditions: { "qwen3-serena": { current_build: build } } }),
		);
		await env.R2_BUCKET.put(
			`${root}/audio/qwen3-serena/builds/${build}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build,
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		expect((await sync([book])).status).toBe(200);
		expect((await search()).books).toEqual([
			expect.objectContaining({ title: "紅樓夢", author: "曹雪芹", current_build: build }),
		]);
		await env.R2_BUCKET.delete(`${root}/manifest.json`);
		expect((await reconcile()).status).toBe(200);
		expect((await search()).books).toEqual([]);
	});
	it("rejects incompatible manifests and oversized sync batches", async () => {
		const key = { author_slug: FALLBACK_AUTHOR, title_slug: FALLBACK_TITLE };
		await env.R2_BUCKET.put(FALLBACK_BOOK_MANIFEST_KEY, JSON.stringify(FALLBACK_BOOK_MANIFEST));
		await env.R2_BUCKET.put(
			FALLBACK_RENDITION_MANIFEST_KEY,
			JSON.stringify({ ...FALLBACK_RENDITION_MANIFEST, build: "0000000000000000" }),
		);
		expect((await sync([key])).status).toBe(409);
		expect((await sync(Array.from({ length: 11 }, () => key))).status).toBe(400);
	});
	it("reconciles at most ten rows per authenticated cursor page", async () => {
		await env.JOB_DB.batch(
			Array.from({ length: 11 }, (_, index) =>
				env.JOB_DB.prepare(`INSERT INTO published_books
			(author_slug,title_slug,author,title,source,rendition,current_build,total_duration_seconds,section_count,has_cover,title_search,author_search,updated_at,revision)
			SELECT ?,title_slug,author,title,source,rendition,current_build,total_duration_seconds,section_count,has_cover,title_search,author_search,updated_at,?
			FROM published_books WHERE author_slug='franz-kafka'`).bind(
					`zz-extra-${String(index).padStart(2, "0")}`,
					crypto.randomUUID(),
				),
			),
		);
		expect((await reconcile(undefined, bindings, "wrong-token")).status).toBe(401);
		const first = await (await reconcile()).json<{
			checked: number;
			removed: number;
			after: { author_slug: string; title_slug: string };
		}>();
		expect(first.checked).toBe(10);
		expect(first.after).toBeTruthy();
		const second = await (await reconcile(first.after)).json<{
			checked: number;
			removed: number;
			after: null;
		}>();
		expect(second.checked).toBe(3);
		expect(second.after).toBeNull();
		expect(first.removed + second.removed).toBe(11);
		expect(
			await env.JOB_DB.prepare("SELECT COUNT(*) count FROM published_books").first("count"),
		).toBe(2);
	});
	it("prunes missing or incompatible manifests but never treats storage failures as invalid publications", async () => {
		await env.R2_BUCKET.delete("books/franz-kafka/the-trial/manifest.json");
		const target = CATALOG.books[1];
		await env.R2_BUCKET.put(
			`books/${target.author_slug}/${target.title_slug}/audio/${target.rendition}/builds/${target.current_build}/rendition-manifest.json`,
			"invalid JSON",
		);
		const broken = new Proxy(env.R2_BUCKET, {
			get(bucket, key) {
				if (key === "get")
					return async () => {
						throw new Error("temporary R2 failure");
					};
				const value = Reflect.get(bucket, key);
				return typeof value === "function" ? value.bind(bucket) : value;
			},
		});
		expect((await reconcile(undefined, { ...bindings, R2_BUCKET: broken })).status).toBe(503);
		expect(
			await env.JOB_DB.prepare("SELECT COUNT(*) count FROM published_books").first("count"),
		).toBe(2);
		expect(await (await reconcile()).json()).toMatchObject({ checked: 2, removed: 2, after: null });
	});
	it("retains missing publications protected by active jobs or deletion guards", async () => {
		await env.JOB_DB.prepare(`INSERT INTO source_books(source_id,title,author,epub_url,author_slug,title_slug,updated_at)
			VALUES('gutenberg:999999','The Trial','Franz Kafka','https://invalid.example','franz-kafka','the-trial','now')`).run();
		await env.JOB_DB.prepare(
			"INSERT INTO generation_starts(id,day,created_at) VALUES('catalog-start','2026-10-24','now')",
		).run();
		await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at)
			VALUES('catalog-job','gutenberg:999999','1234567890abcdef','catalog-start','queued','queued','now','now')`).run();
		await env.JOB_DB.prepare(
			"INSERT INTO book_deletions(author_slug,title_slug,lease_token,lease_until) VALUES('fyodor-dostoevsky','crime-and-punishment','guard','future')",
		).run();
		for (const book of CATALOG.books)
			await env.R2_BUCKET.delete(`books/${book.author_slug}/${book.title_slug}/manifest.json`);
		expect(await (await reconcile()).json()).toMatchObject({ checked: 2, removed: 0 });
		expect(
			await env.JOB_DB.prepare("SELECT COUNT(*) count FROM published_books").first("count"),
		).toBe(2);
	});
	it("does not delete a concurrently replaced row revision", async () => {
		const path = "books/franz-kafka/the-trial/manifest.json";
		await env.R2_BUCKET.delete(path);
		const raced = new Proxy(env.R2_BUCKET, {
			get(bucket, key) {
				if (key === "head")
					return async (objectKey: string) => {
						if (objectKey === path)
							await env.JOB_DB.prepare(
								"UPDATE published_books SET revision='concurrent-recovery' WHERE author_slug='franz-kafka'",
							).run();
						return bucket.head(objectKey);
					};
				const value = Reflect.get(bucket, key);
				return typeof value === "function" ? value.bind(bucket) : value;
			},
		});
		expect(
			await (await reconcile(undefined, { ...bindings, R2_BUCKET: raced })).json(),
		).toMatchObject({ removed: 0 });
		expect(
			await env.JOB_DB.prepare(
				"SELECT revision FROM published_books WHERE author_slug='franz-kafka'",
			).first("revision"),
		).toBe("concurrent-recovery");
	});
	it("does not delete a manifest that reappears before the ETag recheck", async () => {
		const path = "books/franz-kafka/the-trial/manifest.json";
		const original = await (await env.R2_BUCKET.get(path))!.text();
		await env.R2_BUCKET.delete(path);
		const raced = new Proxy(env.R2_BUCKET, {
			get(bucket, key) {
				if (key === "head")
					return async (objectKey: string) => {
						if (objectKey === path) await bucket.put(path, original);
						return bucket.head(objectKey);
					};
				const value = Reflect.get(bucket, key);
				return typeof value === "function" ? value.bind(bucket) : value;
			},
		});
		expect(
			await (await reconcile(undefined, { ...bindings, R2_BUCKET: raced })).json(),
		).toMatchObject({ removed: 0 });
		expect(
			await env.JOB_DB.prepare("SELECT COUNT(*) count FROM published_books").first("count"),
		).toBe(2);
	});
	it("atomically clears source availability and retires old jobs so removed books can be generated again", async () => {
		await env.JOB_DB.prepare(`INSERT INTO source_books(source_id,title,author,epub_url,author_slug,title_slug,updated_at)
			VALUES('gutenberg:999998','The Trial','Franz Kafka','https://www.gutenberg.org/ebooks/999998.epub3.images','franz-kafka','the-trial','now')`).run();
		await env.JOB_DB.prepare(
			"INSERT INTO source_tokens(token,source_id) VALUES('trial','gutenberg:999998')",
		).run();
		await env.JOB_DB.prepare(
			"INSERT INTO generation_starts(id,day,created_at) VALUES('retired-start','2026-10-24','now')",
		).run();
		await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at,author_slug,title_slug)
			VALUES('retired-job','gutenberg:999998','1234567890abcdef','retired-start','completed','completed','now','now','franz-kafka','the-trial')`).run();
		const search = async () =>
			(
				await (
					await app.request("/api/v1/source-books?q=trial", {}, bindings)
				).json<{
					books: {
						state: string;
						job_id: string | null;
						author_slug: string | null;
						title_slug: string | null;
					}[];
				}>()
			).books[0];
		expect(await search()).toMatchObject({ state: "available", job_id: "retired-job" });
		await env.R2_BUCKET.delete("books/franz-kafka/the-trial/manifest.json");
		expect(await (await reconcile()).json()).toMatchObject({ removed: 1 });
		expect(await search()).toMatchObject({
			state: "ready_to_generate",
			job_id: null,
			author_slug: null,
			title_slug: null,
		});
		expect(
			await env.JOB_DB.prepare(
				"SELECT stage,attempts FROM generation_jobs WHERE id='retired-job'",
			).first(),
		).toMatchObject({ stage: "deleted", attempts: 3 });
		const created = await app.request(
			"/api/v1/generation-jobs",
			{
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ source_id: "gutenberg:999998" }),
			},
			bindings,
		);
		expect(created.status).toBe(200);
		expect(await created.json()).toMatchObject({ state: "queued" });
	});
	it("supports Chinese title and author queries beyond D1's fifty-byte LIKE pattern limit", async () => {
		const title = "山河天地玄黃".repeat(6),
			author = "古今人物".repeat(6);
		const key = { author_slug: "long-author", title_slug: "long-title" };
		const root = `books/${key.author_slug}/${key.title_slug}`;
		await env.R2_BUCKET.put(
			`${root}/manifest.json`,
			JSON.stringify({
				title,
				author,
				source: "local",
				renditions: { "qwen3-serena": { current_build: FALLBACK_BUILD } },
			}),
		);
		await env.R2_BUCKET.put(
			`${root}/audio/qwen3-serena/builds/${FALLBACK_BUILD}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build: FALLBACK_BUILD,
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		expect((await sync([key])).status).toBe(200);
		for (const query of [
			`q=${encodeURIComponent(title)}`,
			`author=${encodeURIComponent(author)}`,
		]) {
			const response = await app.request(`/api/v1/catalog?${query}`, {}, env);
			expect(response.status).toBe(200);
			expect((await response.json<CatalogResponse>()).books).toEqual([
				expect.objectContaining({ title, author }),
			]);
		}
	});
});
