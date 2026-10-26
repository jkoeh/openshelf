import { env, fetchMock } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";
import { sourceTitleSlug, gutenbergMirrorUrl } from "../../src/utils/source-editions";

const ownerToken = "test-owner-token-longer-than-twenty-four";
const pcToken = "test-consumer-token-longer-than-twenty-four";
let searchAllowed = true;
let createAllowed = true;
let origin = "https://test.example";
const limiter = { limit: async () => ({ success: searchAllowed }) } as RateLimit;
const createLimiter = { limit: async () => ({ success: createAllowed }) } as RateLimit;
const bindings: Env = {
	R2_BUCKET: env.R2_BUCKET,
	JOB_DB: env.JOB_DB,
	OWNER_TOKEN: ownerToken,
	PC_TOKEN: pcToken,
	SEARCH_RATE_LIMITER: limiter,
	CREATE_RATE_LIMITER: createLimiter,
	AUTH_RATE_LIMITER: limiter,
};
const request = (path: string, method = "GET", body?: unknown, token?: string) =>
	app.request(
		`${origin}/api/v1${path}`,
		{
			method,
			headers: {
				...(body ? { "Content-Type": "application/json" } : {}),
				...(token ? { Authorization: `Bearer ${token}` } : {}),
			},
			...(body ? { body: JSON.stringify(body) } : {}),
		},
		bindings,
	);
const source = (id: number, title: string, author = "Lewis Carroll") => ({
	source_id: `gutenberg:${id}`,
	title,
	author,
	epub_url: `https://www.gutenberg.org/ebooks/${id}.epub3.images`,
});
const sync = async (
	books: {
		source_id: string;
		title: string;
		author: string;
		epub_url: string;
		language?: "en" | "zh";
		provenance?: object;
	}[],
) => request("/internal/source-books/sync", "POST", { books }, pcToken);
const uploadReading = (sourceId: string, body = "PK\x03\x04reading-copy") =>
	app.request(
		`${origin}/api/v1/internal/source-books/${encodeURIComponent(sourceId)}/epub`,
		{
			method: "POST",
			body,
			headers: {
				Authorization: `Bearer ${pcToken}`,
				"Content-Type": "application/epub+zip",
				"Content-Length": String(body.length),
			},
		},
		bindings,
	);
const create = async (id: number, token = ownerToken) =>
	request("/generation-jobs", "POST", { source_id: `gutenberg:${id}` }, token);
const claim = async (expressive = false, mandarin = false) =>
	request("/internal/generation-jobs/claim", "POST", { expressive, mandarin }, pcToken);

beforeEach(async () => {
	searchAllowed = true;
	createAllowed = true;
	origin = `https://${crypto.randomUUID()}.example`;
	await env.JOB_DB.exec(
		"DELETE FROM generation_jobs; DELETE FROM generation_starts; DELETE FROM source_tokens; DELETE FROM source_books;",
	);
});

describe("source search and generation API", () => {
	it("browses only cached public Chinese editions with current counts and stable pages", async () => {
		await sync([
			{ ...source(23962, "西遊記"), language: "zh" },
			{ ...source(24264, "紅樓夢"), language: "zh" },
			{ ...source(7337, "道德經"), language: "zh" },
			{ ...source(11, "Alice"), language: "en" },
		]);
		await uploadReading("gutenberg:23962");
		await uploadReading("gutenberg:24264");
		await uploadReading("gutenberg:11");
		await env.JOB_DB.prepare(
			"INSERT INTO source_books(source_id,title,author,epub_url,updated_at,language,epub_sha256) VALUES('ownerupload:hidden','Private','Owner','https://example.com/private','2026-10-26','zh','hash')",
		).run();
		const first = await request("/source-books?language=zh&limit=1");
		expect(first.headers.get("Cache-Control")).toBe("no-store");
		const page1 = await first.json<{
			total: number;
			page: number;
			books: { source_id: string; reading_cached: boolean }[];
		}>();
		expect(page1).toMatchObject({ total: 2, page: 1, books: [{ reading_cached: true }] });
		const page2 = await (await request("/source-books?q=&language=zh&limit=1&page=2")).json<
			typeof page1
		>();
		expect(new Set([...page1.books, ...page2.books].map((book) => book.source_id))).toEqual(
			new Set(["gutenberg:23962", "gutenberg:24264"]),
		);
		await uploadReading("gutenberg:7337");
		expect(await (await request("/source-books?language=zh")).json()).toMatchObject({ total: 3 });
		expect((await request("/source-books")).status).toBe(400);
		expect((await request("/source-books?q=西&language=zh")).status).toBe(400);
		expect(
			(
				await env.JOB_DB.prepare("SELECT COUNT(*) total FROM generation_jobs").first<{
					total: number;
				}>()
			)?.total,
		).toBe(0);
	});
	it("finds 三国志演义 using the common simplified title without changing edition metadata", async () => {
		await sync([{ ...source(23950, "三國志演義"), language: "zh" }]);
		const found = await (await request(`/source-books?q=${encodeURIComponent("三国演义")}`)).json<{
			books: { source_id: string; title: string }[];
		}>();
		expect(found.books).toEqual([
			expect.objectContaining({ source_id: "gutenberg:23950", title: "三國志演義" }),
		]);
	});
	it("indexes, reads, claims and publishes an exact Standard Ebooks edition", async () => {
		const sourceId = "standardebooks:lewis-carroll/alice/john-tenniel";
		const epubUrl =
			"https://standardebooks.org/ebooks/lewis-carroll/alice/john-tenniel/downloads/lewis-carroll_alice_john-tenniel.epub?source=download";
		expect(
			(
				await sync([
					{ source_id: sourceId, title: "Alice", author: "Lewis Carroll", epub_url: epubUrl },
				])
			).status,
		).toBe(200);
		const found = await (await request("/source-books?q=alice")).json<{
			books: { source_id: string }[];
		}>();
		expect(found.books[0].source_id).toBe(sourceId);
		const route = `/source-books/${encodeURIComponent(sourceId)}/epub`;
		expect((await request(route)).headers.get("Location")).toBe(epubUrl);
		expect((await request(route + "?inline=1")).status).toBe(502);
		const upload = (body: string, token = pcToken, length = body.length) =>
			app.request(
				`${origin}/api/v1/internal/source-books/${encodeURIComponent(sourceId)}/epub`,
				{
					method: "POST",
					body,
					headers: {
						Authorization: `Bearer ${token}`,
						"Content-Type": "application/epub+zip",
						"Content-Length": String(length),
					},
				},
				{
					R2_BUCKET: env.R2_BUCKET,
					JOB_DB: env.JOB_DB,
					PC_TOKEN: pcToken,
					AUTH_RATE_LIMITER: limiter,
				},
			);
		expect((await upload("PK\x03\x04epub-bytes", "wrong-token")).status).toBe(401);
		expect((await upload("<html>interstitial</html>")).status).toBe(400);
		expect((await upload("PK\x03\x04", pcToken, 32 * 1024 * 1024 + 1)).status).toBe(413);
		expect((await upload("PK\x03\x04epub-bytes")).status).toBe(200);
		const response = await request(route + "?inline=1");
		expect(response.status).toBe(200);
		expect(await response.text()).toBe("PK\x03\x04epub-bytes");
		expect(response.headers.get("Cache-Control")).toBe("no-store");
		const head = await app.request(
			`${origin}/api/v1${route}?inline=1`,
			{ method: "HEAD" },
			{ R2_BUCKET: env.R2_BUCKET, JOB_DB: env.JOB_DB, SEARCH_RATE_LIMITER: limiter },
		);
		expect(head.status).toBe(200);
		expect(await head.text()).toBe("");
		for (const invalid of [
			epubUrl.replace("/alice/", "/other/"),
			epubUrl + "&evil=1",
			epubUrl.replace("standardebooks.org", "evil.example"),
		]) {
			expect(
				(
					await sync([
						{ source_id: sourceId, title: "Alice", author: "Lewis Carroll", epub_url: invalid },
					])
				).status,
			).toBe(400);
		}
		expect((await request("/generation-jobs", "POST", { source_id: sourceId })).status).toBe(200);
		const job = (
			await (
				await claim()
			).json<{ job: { id: string; source_id: string; build_id: string; lease_token: string } }>()
		).job;
		expect(job.source_id).toBe(sourceId);
		const title = await sourceTitleSlug("alice", sourceId);
		const root = `books/lewis-carroll/${title}`;
		const prefix = `${root}/audio/kokoro-af-heart/builds/${job.build_id}`;
		await env.R2_BUCKET.put(
			`${root}/manifest.json`,
			JSON.stringify({
				title: "Alice",
				author: "Lewis Carroll",
				source: "standard-ebooks",
				renditions: { "kokoro-af-heart": { current_build: job.build_id } },
			}),
		);
		await env.R2_BUCKET.put(
			`${prefix}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build: job.build_id,
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		await env.R2_BUCKET.put(`${prefix}/section_data.json`, "{}");
		await env.R2_BUCKET.put(`${prefix}/section-01.m4a`, "audio");
		await env.R2_BUCKET.put(
			"catalog.json",
			JSON.stringify({
				books: [{ author_slug: "lewis-carroll", title_slug: title, current_build: job.build_id }],
			}),
		);
		const done = {
			lease_token: job.lease_token,
			success: true,
			author_slug: "lewis-carroll",
			title_slug: title,
		};
		expect(
			(
				await request(
					`/internal/generation-jobs/${job.id}/finish`,
					"POST",
					{ ...done, title_slug: "alice-g11" },
					pcToken,
				)
			).status,
		).toBe(409);
		expect(
			(await request(`/internal/generation-jobs/${job.id}/finish`, "POST", done, pcToken)).status,
		).toBe(200);
	});
	it("reads Gutenberg from R2 while upstream access and audio starts are unavailable", async () => {
		await sync([source(11, "Alice")]);
		expect((await request("/source-books/gutenberg%3A11/epub?inline=1")).status).toBe(502);
		expect((await uploadReading("gutenberg:11")).status).toBe(200);
		createAllowed = false;
		fetchMock.activate();
		fetchMock.disableNetConnect();
		try {
			await env.JOB_DB.prepare(
				"UPDATE source_books SET epub_url='https://offline.example/book' WHERE source_id='gutenberg:11'",
			).run();
			const response = await request("/source-books/gutenberg%3A11/epub?inline=1");
			expect(response.status).toBe(200);
			expect(response.headers.get("Content-Type")).toBe("application/epub+zip");
			expect(await response.text()).toBe("PK\x03\x04reading-copy");
			expect(response.headers.get("X-Content-SHA256")).toMatch(/^[a-f0-9]{64}$/);
			const direct = await request("/source-books/gutenberg%3A11/epub");
			expect(direct.status).toBe(200);
			expect(await direct.text()).toBe("PK\x03\x04reading-copy");
			expect(
				await env.JOB_DB.prepare("SELECT COUNT(*) count FROM generation_starts").first("count"),
			).toBe(0);
			fetchMock.assertNoPendingInterceptors();
		} finally {
			fetchMock.deactivate();
		}
	});
	it("conditionally preserves validated bytes and cached provenance on refresh", async () => {
		const book = {
			...source(11, "Alice"),
			provenance: { rights_statement: "Public domain in the USA." },
		};
		await sync([book]);
		expect((await uploadReading(book.source_id)).status).toBe(200);
		expect((await uploadReading(book.source_id)).status).toBe(200);
		expect((await uploadReading(book.source_id, "PK\x03\x04different-edition")).status).toBe(409);
		await sync([{ ...book, language: "zh", provenance: {} }]);
		const stored = await env.JOB_DB.prepare(
			"SELECT language,provenance,epub_sha256 FROM source_books WHERE source_id=?",
		)
			.bind(book.source_id)
			.first<{ language: string; provenance: string; epub_sha256: string }>();
		expect(stored?.language).toBe("en");
		expect(JSON.parse(stored!.provenance)).toEqual(book.provenance);
		expect(stored!.epub_sha256).toMatch(/^[a-f0-9]{64}$/);
		expect(await (await request("/source-books/gutenberg%3A11/epub?inline=1")).text()).toBe(
			"PK\x03\x04reading-copy",
		);
	});
	it("indexes Chinese Wikisource snapshots, filters language and claims Mandarin jobs", async () => {
		const sourceId = "wikisource:zh:1:10:1234567890abcdef";
		const book = {
			source_id: sourceId,
			title: "古书天地玄黄宇宙洪荒",
			author: "古人",
			language: "zh" as const,
			epub_url: "https://zh.wikisource.org/w/index.php?oldid=10",
			provenance: { rights_statement: "Public domain in the USA." },
		};
		expect((await sync([book])).status).toBe(200);
		expect((await sync([{ ...book, epub_url: book.epub_url.replace("10", "11") }])).status).toBe(
			400,
		);
		expect((await sync([{ ...book, language: "en" }])).status).toBe(400);
		expect((await request(`/source-books/${encodeURIComponent(sourceId)}/epub`)).status).toBe(502);
		expect((await uploadReading(sourceId)).status).toBe(200);
		const found = await (await request("/source-books?q=天地&language=zh")).json<{
			books: { language: string; reading_cached: boolean }[];
		}>();
		expect(found.books).toHaveLength(1);
		expect(found.books[0]).toMatchObject({ language: "zh", reading_cached: true });
		expect(
			(
				await (
					await request("/source-books?q=天地玄黄宇宙&language=zh")
				).json<{ books: object[] }>()
			).books,
		).toHaveLength(1);
		expect(
			(await (await request("/source-books?q=天地&language=en")).json<{ books: object[] }>()).books,
		).toHaveLength(0);
		expect(
			(
				await request(
					"/generation-jobs",
					"POST",
					{ source_id: sourceId, mode: "expressive" },
					ownerToken,
				)
			).status,
		).toBe(400);
		expect(
			await env.JOB_DB.prepare("SELECT COUNT(*) count FROM generation_starts").first("count"),
		).toBe(0);
		expect((await request("/generation-jobs", "POST", { source_id: sourceId })).status).toBe(200);
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
		const job = (
			await (
				await claim(false, true)
			).json<{
				job: {
					id: string;
					build_id: string;
					lease_token: string;
					language: string;
					epub_sha256: string;
					provenance: object;
				};
			}>()
		).job;
		expect(job.language).toBe("zh");
		expect(job.epub_sha256).toMatch(/^[a-f0-9]{64}$/);
		expect(job.provenance).toEqual(book.provenance);
		const title = await sourceTitleSlug("", sourceId);
		const root = `books/unknown/${title}`;
		const prefix = `${root}/audio/qwen3-serena/builds/${job.build_id}`;
		await env.R2_BUCKET.put(
			`${root}/manifest.json`,
			JSON.stringify({
				title: book.title,
				author: book.author,
				source: "wikisource",
				renditions: { "qwen3-serena": { current_build: job.build_id } },
			}),
		);
		await env.R2_BUCKET.put(
			`${prefix}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build: job.build_id,
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		await env.R2_BUCKET.put(`${prefix}/section_data.json`, "{}");
		await env.R2_BUCKET.put(`${prefix}/section-01.m4a`, "audio");
		await env.R2_BUCKET.put(
			"catalog.json",
			JSON.stringify({
				books: [{ author_slug: "unknown", title_slug: title, current_build: job.build_id }],
			}),
		);
		expect(
			(
				await request(
					`/internal/generation-jobs/${job.id}/finish`,
					"POST",
					{
						lease_token: job.lease_token,
						success: true,
						author_slug: "unknown",
						title_slug: title,
					},
					pcToken,
				)
			).status,
		).toBe(200);
	});
	it("downloads an indexed EPUB independently of failed audio, PC credentials, and quotas", async () => {
		await sync([source(2554, "Crime and Punishment")]);
		const made = await (await create(2554)).json<{ id: string }>();
		await env.JOB_DB.prepare(
			"UPDATE generation_jobs SET state='failed',stage='failed',error_code='BookTooLong' WHERE id=?",
		)
			.bind(made.id)
			.run();
		const searched = await (await request("/source-books?q=punishment")).json<{
			books: { job_error_code: string }[];
		}>();
		expect(searched.books[0].job_error_code).toBe("BOOK_TOO_LONG");
		createAllowed = false;
		const response = await app.request(
			`${origin}/api/v1/source-books/gutenberg%3A2554/epub`,
			{},
			{
				R2_BUCKET: env.R2_BUCKET,
				JOB_DB: env.JOB_DB,
				SEARCH_RATE_LIMITER: limiter,
			},
		);
		expect(response.status).toBe(302);
		expect(response.headers.get("Location")).toBe(
			gutenbergMirrorUrl(new URL(source(2554, "Crime").epub_url), "gutenberg:2554")!.href,
		);
		expect(response.headers.get("Cache-Control")).toBe("no-store");
		const job = await (await request(`/generation-jobs/${made.id}`)).json<{ state: string }>();
		expect(job.state).toBe("failed");
		expect(
			await env.JOB_DB.prepare("SELECT COUNT(*) count FROM generation_starts").first("count"),
		).toBe(1);
	});

	it("validates and rate limits source EPUB lookups without redirecting invalid editions", async () => {
		await sync([source(2554, "Crime and Punishment")]);
		const path = "/source-books/gutenberg%3A2554/epub";
		expect((await request("/source-books/gutenberg%3A999999/epub")).status).toBe(404);
		expect((await request("/source-books/not-a-source/epub")).status).toBe(400);
		for (const epubUrl of [
			"not-a-url",
			"https://evil.example/ebooks/2554.epub",
			"https://www.gutenberg.org/ebooks/11.epub3.images",
			"https://www.gutenberg.org/ebooks/2554.html",
			"https://user:password@www.gutenberg.org/ebooks/2554.epub3.images",
			"https://www.gutenberg.org/ebooks/2554.epub3.images?redirect=evil",
			"https://www.gutenberg.org/ebooks/2554.epub3.images#fragment",
			"http://www.gutenberg.org/ebooks/2554.epub3.images",
		]) {
			await env.JOB_DB.prepare(
				"UPDATE source_books SET epub_url=? WHERE source_id='gutenberg:2554'",
			)
				.bind(epubUrl)
				.run();
			const response = await request(path);
			expect(response.status).toBe(400);
			expect(response.headers.get("Location")).toBeNull();
		}
		await env.JOB_DB.prepare(
			"UPDATE source_books SET epub_url='https://www.gutenberg.org/cache/epub/2554/pg2554-images-3.epub' WHERE source_id='gutenberg:2554'",
		).run();
		expect((await request(path)).status).toBe(302);
		searchAllowed = false;
		expect((await request(path)).status).toBe(429);
		expect(
			(await app.request(`${origin}/api/v1${path}`, {}, { R2_BUCKET: env.R2_BUCKET })).status,
		).toBe(503);
	});

	it("shows a private bounded queue and claims high-priority queued work first", async () => {
		await sync([source(11, "Alice"), source(12, "A Second Book")]);
		const first = await (await create(11)).json<{ id: string }>();
		const second = await (await create(12)).json<{ id: string }>();
		const path = "/admin/generation-jobs";
		expect((await request(path)).status).toBe(401);
		expect((await request(path, "GET", undefined, pcToken)).status).toBe(401);
		expect(
			(await request(`${path}/${second.id}/priority`, "POST", { priority: "urgent" }, ownerToken))
				.status,
		).toBe(400);
		const prioritized = await request(
			`${path}/${second.id}/priority`,
			"POST",
			{ priority: "high" },
			ownerToken,
		);
		expect(prioritized.status).toBe(200);
		expect((await prioritized.json<{ priority: number }>()).priority).toBe(1);
		const listed = await request(path, "GET", undefined, ownerToken);
		expect(listed.status).toBe(200);
		expect(listed.headers.get("Cache-Control")).toBe("no-store");
		const body = await listed.json<{
			active: {
				id: string;
				title: string;
				priority: number;
				state: string;
				lease_until: string | null;
			}[];
			recent: unknown[];
		}>();
		expect(body.active.map((job) => job.id)).toEqual([second.id, first.id]);
		expect(body.active[0]).toMatchObject({
			title: "A Second Book",
			priority: 1,
			state: "queued",
			lease_until: null,
		});
		expect(body.recent).toEqual([]);
		expect(JSON.stringify(body)).not.toContain("lease_token");
		expect(JSON.stringify(body)).not.toContain("build_id");
		expect(JSON.stringify(body)).not.toContain("epub_url");
		const claimed = await (await claim()).json<{ job: { id: string } }>();
		expect(claimed.job.id).toBe(second.id);
		expect(
			(await request(`${path}/${second.id}/priority`, "POST", { priority: "normal" }, ownerToken))
				.status,
		).toBe(409);
		const running = await (await request(path, "GET", undefined, ownerToken)).json<{
			active: { id: string; lease_until: string | null }[];
		}>();
		expect(running.active[0].id).toBe(second.id);
		expect(running.active[0].lease_until).toBeTruthy();
		expect(
			(await request(`/generation-jobs/${second.id}/cancel`, "POST", undefined, ownerToken)).status,
		).toBe(200);
		const after = await (await request(path, "GET", undefined, ownerToken)).json<{
			recent: { id: string; state: string }[];
		}>();
		expect(after.recent[0]).toMatchObject({ id: second.id, state: "canceled" });
		searchAllowed = false;
		expect((await request(path, "GET", undefined, ownerToken)).status).toBe(429);
	});
	it("bounds owner history to the twenty newest terminal jobs", async () => {
		await sync([source(11, "Alice")]);
		await env.JOB_DB.prepare(
			"INSERT INTO generation_starts(id,day,created_at) VALUES('fixture-start','2026-10-05','2026-10-05T00:00:00Z')",
		).run();
		const ids: string[] = [];
		for (let index = 0; index < 25; index++) {
			const id = crypto.randomUUID();
			ids.push(id);
			await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at)
				VALUES(?,'gutenberg:11','1234567890abcdef','fixture-start','completed','completed',?,?)`)
				.bind(
					id,
					new Date(Date.UTC(2026, 9, 5, 0, index)).toISOString(),
					new Date(Date.UTC(2026, 9, 5, 0, index)).toISOString(),
				)
				.run();
		}
		const response = await request("/admin/generation-jobs", "GET", undefined, ownerToken);
		const body = await response.json<{ active: unknown[]; recent: { id: string }[] }>();
		expect(body.active).toEqual([]);
		expect(body.recent).toHaveLength(20);
		expect(body.recent[0].id).toBe(ids[24]);
		expect(body.recent[19].id).toBe(ids[5]);
	});

	it("keeps published availability alongside a failed regeneration job", async () => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string }>();
		await env.JOB_DB.prepare(
			"UPDATE source_books SET author_slug='lewis-carroll',title_slug='alice-g11' WHERE source_id='gutenberg:11'",
		).run();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(made.id)
			.run();
		const result = await request("/source-books?q=alice");
		const body = await result.json<{
			books: { state: string; job_state: string; job_id: string; job_updated_at: string }[];
		}>();
		expect(body.books[0]).toMatchObject({
			state: "available",
			job_state: "failed",
			job_id: made.id,
		});
		expect(body.books[0].job_updated_at).toBeTruthy();
	});

	it("returns the newest job ID and state after another request for the same edition", async () => {
		await sync([source(11, "Alice")]);
		const first = await (await create(11)).json<{ id: string }>();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(first.id)
			.run();
		const second = await (await create(11)).json<{ id: string }>();
		expect(second.id).not.toBe(first.id);
		const result = await request("/source-books?q=alice");
		const body = await result.json<{ books: { job_id: string; job_state: string }[] }>();
		expect(body.books[0]).toMatchObject({ job_id: second.id, job_state: "queued" });
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(second.id)
			.run();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='queued',stage='queued' WHERE id=?")
			.bind(first.id)
			.run();
		origin = `https://${crypto.randomUUID()}.example`;
		const active = await (await request("/source-books?q=alice")).json<{
			books: { job_id: string; job_state: string }[];
		}>();
		expect(active.books[0]).toMatchObject({ job_id: first.id, job_state: "queued" });
		const activePlan = await env.JOB_DB.prepare(
			"EXPLAIN QUERY PLAN SELECT id FROM generation_jobs WHERE source_id=? AND state IN ('queued','running') LIMIT 1",
		)
			.bind("gutenberg:11")
			.all<{ detail: string }>();
		expect(activePlan.results.some((row) => row.detail.includes("one_active_generation"))).toBe(
			true,
		);
		const latestPlan = await env.JOB_DB.prepare(
			"EXPLAIN QUERY PLAN SELECT id FROM generation_jobs WHERE source_id=? ORDER BY created_at DESC LIMIT 1",
		)
			.bind("gutenberg:11")
			.all<{ detail: string }>();
		expect(latestPlan.results.some((row) => row.detail.includes("latest_source_job"))).toBe(true);
		expect(
			[...activePlan.results, ...latestPlan.results].some((row) =>
				row.detail.includes("USE TEMP B-TREE"),
			),
		).toBe(false);
	});
	it("keeps browser admin identity closed when Google is unconfigured or invalid", async () => {
		expect((await request("/admin/me", "GET", undefined, ownerToken)).status).toBe(503);
		const configured = {
			...bindings,
			GOOGLE_CLIENT_ID: "openshelf-test.apps.googleusercontent.com",
		};
		const invalid = await app.request(
			`${origin}/api/v1/admin/me`,
			{
				headers: { Authorization: "Bearer forged.google.token" },
			},
			configured,
		);
		expect(invalid.status).toBe(401);
		expect(invalid.headers.get("Cache-Control")).toBe("no-store");
	});
	it("ranks prefix and typo suggestions and blocks a search before D1 work", async () => {
		expect((await sync([source(11, "Alice's Adventures in Wonderland")])).status).toBe(200);
		const prefix = await request("/source-books?q=ali");
		expect((await prefix.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:11",
		);
		const typo = await request("/source-books?q=alcie");
		expect((await typo.json<{ books: unknown[] }>()).books).toHaveLength(1);
		searchAllowed = false;
		expect((await request("/source-books?q=alice")).status).toBe(429);
	});

	it("reads current index changes on every identical public search without bypassing rate limits", async () => {
		await sync([source(71, "Zephyr Atlas")]);
		const first = await request("/source-books?q=Zephyr&limit=1");
		expect(first.status).toBe(200);
		expect(first.headers.get("Cache-Control")).toBe("no-store");
		expect((await first.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:71",
		);
		await env.JOB_DB.exec("DELETE FROM source_tokens; DELETE FROM source_books;");
		const fresh = await request("/source-books?q=Zephyr&limit=1");
		expect(fresh.headers.get("Cache-Control")).toBe("no-store");
		expect((await fresh.json<{ books: unknown[] }>()).books).toEqual([]);
		await sync([source(72, "Zephyr Restored")]);
		const imported = await request("/source-books?q=Zephyr&limit=1");
		expect((await imported.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:72",
		);
		searchAllowed = false;
		expect((await request("/source-books?q=zephyr&limit=1")).status).toBe(429);
	});
	it("finds legacy Chinese titles in either script while preserving exact source metadata", async () => {
		const book = { ...source(24264, "紅樓夢", "曹雪芹"), language: "zh" as const };
		await sync([book]);
		// Simulate an already imported edition before normalized tokens existed.
		await env.JOB_DB.prepare("DELETE FROM source_tokens WHERE source_id=?")
			.bind(book.source_id)
			.run();
		await env.JOB_DB.batch(
			["紅樓夢", "紅樓", "樓夢", "曹雪芹", "曹雪", "雪芹"].map((token) =>
				env.JOB_DB.prepare("INSERT INTO source_tokens(token,source_id) VALUES(?,?)").bind(
					token,
					book.source_id,
				),
			),
		);
		for (const q of ["红楼梦", "紅樓夢", "红樓梦", "楼梦", "樓夢", "曹雪芹"]) {
			const response = await request(`/source-books?q=${encodeURIComponent(q)}&language=zh`);
			expect(response.status).toBe(200);
			const result = await response.json<{
				books: { source_id: string; title: string; author: string }[];
			}>();
			expect(result.books).toHaveLength(1);
			expect(result.books[0]).toMatchObject({
				source_id: book.source_id,
				title: "紅樓夢",
				author: "曹雪芹",
			});
		}
		const absent = await request(`/source-books?q=${encodeURIComponent("红楼外传")}&language=zh`);
		expect((await absent.json<{ books: unknown[] }>()).books).toEqual([]);
	});
	it("normalizes both new simplified editions and regional traditional variants", async () => {
		await sync([
			{ ...source(1001, "西游记", "吴承恩"), language: "zh" },
			{ ...source(1002, "臺灣裏巷", "作者"), language: "zh" },
		]);
		for (const [q, id] of [
			["西遊記", "gutenberg:1001"],
			["吳承恩", "gutenberg:1001"],
			["里巷", "gutenberg:1002"],
			["裡巷", "gutenberg:1002"],
		]) {
			const response = await request(`/source-books?q=${encodeURIComponent(q)}`);
			expect(
				(await response.json<{ books: { source_id: string }[] }>()).books.map(
					(book) => book.source_id,
				),
			).toEqual([id]);
		}
	});
	it("reflects reading-cache and job-state updates immediately for the same search", async () => {
		await sync([source(11, "Alice")]);
		const search = async () =>
			(
				await (
					await request("/source-books?q=alice")
				).json<{ books: { reading_cached: boolean; job_state: string | null }[] }>()
			).books[0];
		expect(await search()).toMatchObject({ reading_cached: false, job_state: null });
		await uploadReading("gutenberg:11");
		const made = await (await create(11)).json<{ id: string }>();
		expect(await search()).toMatchObject({ reading_cached: true, job_state: "queued" });
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(made.id)
			.run();
		expect(await search()).toMatchObject({ reading_cached: true, job_state: "failed" });
	});
	it("finds an exact Chinese title beyond eighty earlier matches for its first two characters", async () => {
		for (let batch = 0; batch < 2; batch++) {
			await sync(
				Array.from({ length: 50 }, (_, index) => ({
					...source(100000 + batch * 50 + index, `紅樓傳奇${batch * 50 + index}`),
					language: "zh" as const,
				})),
			);
		}
		await sync([{ ...source(24264, "紅樓夢", "曹雪芹"), language: "zh" }]);
		for (const q of ["红楼梦", "紅樓夢"]) {
			const response = await request(`/source-books?q=${encodeURIComponent(q)}`);
			expect(
				(await response.json<{ books: { source_id: string }[] }>()).books.map(
					(book) => book.source_id,
				),
			).toEqual(["gutenberg:24264"]);
		}
	});
	it("keeps invalid and unavailable search responses uncached", async () => {
		const invalid = await request("/source-books?q=a");
		expect(invalid.status).toBe(400);
		expect(invalid.headers.get("Cache-Control")).toBe("no-store");
		const unavailable = await app.request(
			`${origin}/api/v1/source-books?q=alice`,
			{},
			{ R2_BUCKET: env.R2_BUCKET },
		);
		expect(unavailable.status).toBe(503);
		expect(unavailable.headers.get("Cache-Control")).toBe("no-store");
	});
	it("never discovers hidden owner originals even if tokens were accidentally added", async () => {
		const id = "ownerupload:11111111-1111-4111-8111-111111111111";
		await env.JOB_DB.prepare(
			"INSERT INTO source_books(source_id,title,author,epub_url,updated_at) VALUES(?,?,?,?,?)",
		)
			.bind(
				id,
				"Private manuscript",
				"Owner",
				"https://invalid.example/private",
				new Date().toISOString(),
			)
			.run();
		await env.JOB_DB.prepare("INSERT INTO source_tokens(token,source_id) VALUES('private',?)")
			.bind(id)
			.run();
		const response = await request("/source-books?q=private");
		expect((await response.json<{ books: unknown[] }>()).books).toEqual([]);
	});

	it("keeps autocomplete bounded and uses the token index for candidate lookup", async () => {
		for (let page = 0; page < 4; page++) {
			const batch = Array.from({ length: 50 }, (_, n) =>
				source(page * 50 + n + 1, `Alice story ${page * 50 + n}`),
			);
			expect((await sync(batch)).status).toBe(200);
		}
		const response = await request("/source-books?q=story&limit=10");
		expect(response.status).toBe(200);
		expect((await response.json<{ books: unknown[] }>()).books).toHaveLength(10);
		const plan = await env.JOB_DB.prepare(
			"EXPLAIN QUERY PLAN SELECT source_id FROM source_tokens WHERE token >= ? AND token < ? LIMIT 80",
		)
			.bind("al", "al\uffff")
			.all<{ detail: string }>();
		expect(plan.results.some((row) => row.detail.includes("COVERING INDEX"))).toBe(true);
	});

	it("finds a full-prefix title beyond eighty earlier two-letter candidates", async () => {
		for (let page = 0; page < 2; page++) {
			const batch = Array.from({ length: 50 }, (_, n) =>
				source(page * 50 + n + 1, `Alabaster ${page * 50 + n}`),
			);
			expect((await sync(batch)).status).toBe(200);
		}
		await sync([source(111, "Alice in Wonderland")]);
		const found = await request("/source-books?q=alice");
		expect((await found.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:111",
		);
		const typo = await request("/source-books?q=alcie");
		expect((await typo.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:111",
		);
	});

	it("uses a distinctive word in multiword title search", async () => {
		for (let page = 0; page < 2; page++) {
			const batch = Array.from({ length: 50 }, (_, n) =>
				source(page * 50 + n + 1, `The Abacus ${page * 50 + n}`),
			);
			expect((await sync(batch)).status).toBe(200);
		}
		await sync([source(111, "The Great Gatsby")]);
		const found = await request("/source-books?q=the%20great%20gatsby");
		expect((await found.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:111",
		);
	});

	it("rejects invalid credentials, arbitrary sources, and untrusted URLs", async () => {
		expect(
			(await sync([{ ...source(11, "Alice"), epub_url: "https://evil.example/11.epub" }])).status,
		).toBe(400);
		expect(
			(
				await sync([
					{ ...source(11, "Alice"), epub_url: "https://www.gutenberg.org/ebooks/12.epub" },
				])
			).status,
		).toBe(400);
		expect((await sync([source(11, "Alice")])).status).toBe(200);
		expect((await create(11, "bad-token")).status).toBe(401);
		expect(
			(
				await request(
					"/generation-jobs",
					"POST",
					{ source_id: "https://evil.example/book.epub" },
					ownerToken,
				)
			).status,
		).toBe(400);
		expect((await create(999)).status).toBe(404);
	});

	it("limits failed credentials without throttling valid PC operations", async () => {
		searchAllowed = false;
		expect((await sync([source(11, "Alice")])).status).toBe(200);
		expect((await create(11)).status).toBe(200);
		expect((await claim()).status).toBe(200);
		expect((await create(11, "wrong-token")).status).toBe(429);
	});

	it("deduplicates concurrently and allows more than two daily starts", async () => {
		await sync([
			source(11, "Alice"),
			source(12, "Book Two"),
			source(13, "Book Three"),
			source(14, "Book Four"),
		]);
		const pair = await Promise.all([create(11), create(11)]);
		expect(pair.map((r) => r.status)).toEqual([200, 200]);
		const ids = await Promise.all(pair.map(async (r) => (await r.json<{ id: string }>()).id));
		expect(ids[0]).toBe(ids[1]);
		expect((await create(12)).status).toBe(200);
		expect((await create(13)).status).toBe(200);
		expect((await create(14)).status).toBe(429); // Three queued jobs remains the queue cap.
		const count = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_jobs").first<{
			n: number;
		}>();
		expect(count?.n).toBe(3);
	});

	it("atomically admits the 300th daily start and rejects the 301st", async () => {
		await sync([source(11, "Alice"), source(12, "Book Two")]);
		const day = new Date().toISOString().slice(0, 10);
		await env.JOB_DB.batch(
			Array.from({ length: 299 }, () =>
				env.JOB_DB.prepare("INSERT INTO generation_starts(id,day,created_at) VALUES(?,?,?)").bind(
					crypto.randomUUID(),
					day,
					new Date().toISOString(),
				),
			),
		);
		const results = await Promise.all([create(11), create(12)]);
		expect(results.map((result) => result.status).sort()).toEqual([200, 429]);
		const used = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(day)
			.first<{ n: number }>();
		expect(used?.n).toBe(300);
	});

	it("allows capped public requests but keeps regeneration owner-only", async () => {
		await sync([source(11, "Alice")]);
		createAllowed = false;
		expect((await request("/generation-jobs", "POST", { source_id: "gutenberg:11" })).status).toBe(
			429,
		);
		const before = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first<{
			n: number;
		}>();
		expect(before?.n).toBe(0);
		createAllowed = true;
		const response = await request("/generation-jobs", "POST", { source_id: "gutenberg:11" });
		expect(response.status).toBe(200);
		expect((await response.json<{ state: string }>()).state).toBe("queued");
		expect(
			(await request("/generation-jobs", "POST", { source_id: "gutenberg:11", regenerate: true }))
				.status,
		).toBe(401);
	});

	it("requires owner auth for expressive jobs and leases them only to a capable PC", async () => {
		await sync([source(11, "Alice")]);
		const requested = { source_id: "gutenberg:11", mode: "expressive" };
		expect((await request("/generation-jobs", "POST", requested)).status).toBe(401);
		expect((await request("/generation-jobs", "POST", requested, "wrong-token")).status).toBe(401);
		expect(
			(await request("/generation-jobs", "POST", { ...requested, mode: "custom" }, ownerToken))
				.status,
		).toBe(400);
		const made = await request("/generation-jobs", "POST", requested, ownerToken);
		expect(made.status).toBe(200);
		const job = await made.json<{ id: string; mode: string }>();
		expect(job.mode).toBe("expressive");
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
		const current = await (await request("/source-books?q=alice")).json<{
			books: { job_mode: string; job_id: string }[];
		}>();
		expect(current.books[0]).toMatchObject({ job_mode: "expressive", job_id: job.id });
		expect((await request("/generation-jobs", "POST", { source_id: "gutenberg:11" })).status).toBe(
			409,
		);
		const claimed = await (await claim(true)).json<{
			job: { id: string; mode: string; build_id: string } | null;
		}>();
		expect(claimed.job).toMatchObject({ id: job.id, mode: "expressive" });
		const starts = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(new Date().toISOString().slice(0, 10))
			.first<{ n: number }>();
		expect(starts?.n).toBe(1);
	});

	it("cancels queued work without refund, and prevents a claim", async () => {
		await sync([source(11, "Alice")]);
		const job = await (
			await request("/generation-jobs", "POST", { source_id: "gutenberg:11" })
		).json<{ id: string }>();
		const path = `/generation-jobs/${job.id}/cancel`;
		expect((await request(path, "POST")).status).toBe(401);
		const canceled = await request(path, "POST", undefined, ownerToken);
		expect(canceled.status).toBe(200);
		expect((await canceled.json<{ state: string }>()).state).toBe("canceled");
		expect((await request(path, "POST", undefined, ownerToken)).status).toBe(200);
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
		const starts = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first<{
			n: number;
		}>();
		expect(starts?.n).toBe(1);
	});

	it("revokes a running lease on cancellation", async () => {
		await sync([source(11, "Alice")]);
		const job = await (await create(11)).json<{ id: string }>();
		const running = (await (await claim()).json<{ job: { lease_token: string } | null }>()).job!;
		expect(
			(await request(`/generation-jobs/${job.id}/cancel`, "POST", undefined, ownerToken)).status,
		).toBe(200);
		expect(
			(
				await request(
					`/internal/generation-jobs/${job.id}/heartbeat`,
					"POST",
					{ lease_token: running.lease_token },
					pcToken,
				)
			).status,
		).toBe(409);
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
	});

	it.each(["failed", "canceled"])(
		"retries %s jobs with the same build and a fresh lease",
		async (state) => {
			await sync([source(11, "Alice")]);
			const made = await (await create(11)).json<{ id: string }>();
			const first = (
				await (await claim()).json<{ job: { build_id: string; lease_token: string } }>()
			).job;
			if (state === "canceled") {
				expect(
					(await request(`/generation-jobs/${made.id}/cancel`, "POST", undefined, ownerToken))
						.status,
				).toBe(200);
			} else {
				expect(
					(
						await request(
							`/internal/generation-jobs/${made.id}/finish`,
							"POST",
							{
								lease_token: first.lease_token,
								success: false,
								error_code: "PIPELINE_FAILED",
							},
							pcToken,
						)
					).status,
				).toBe(200);
			}
			const path = `/generation-jobs/${made.id}/retry`;
			expect((await request(path, "POST")).status).toBe(401);
			const retried = await request(path, "POST", undefined, ownerToken);
			expect(retried.status).toBe(200);
			expect(await retried.json()).toMatchObject({
				id: made.id,
				state: "queued",
				stage: "queued",
				error_code: null,
			});
			expect((await request(path, "POST", undefined, ownerToken)).status).toBe(409);
			const next = (
				await (
					await claim()
				).json<{ job: { id: string; build_id: string; lease_token: string; attempts: number } }>()
			).job;
			expect(next).toMatchObject({ id: made.id, build_id: first.build_id, attempts: 2 });
			expect(next.lease_token).not.toBe(first.lease_token);
			expect(
				(
					await request(
						`/internal/generation-jobs/${made.id}/heartbeat`,
						"POST",
						{ lease_token: first.lease_token },
						pcToken,
					)
				).status,
			).toBe(409);
			expect(
				await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first(),
			).toMatchObject({ n: 2 });
		},
	);

	it.each(["failed", "canceled"])(
		"rejects %s retry after three attempts without spending a start",
		async (state) => {
			await sync([source(11, "Alice")]);
			const made = await (await create(11)).json<{ id: string }>();
			await env.JOB_DB.prepare("UPDATE generation_jobs SET state=?,attempts=3 WHERE id=?")
				.bind(state, made.id)
				.run();
			expect(
				(await request(`/generation-jobs/${made.id}/retry`, "POST", undefined, ownerToken)).status,
			).toBe(409);
			expect(
				await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first(),
			).toMatchObject({ n: 1 });
		},
	);

	it.each(["failed", "canceled"])(
		"does not spend a daily slot for a full queue or rejected %s retry",
		async (state) => {
			await sync([
				source(11, "One"),
				source(12, "Two"),
				source(13, "Three"),
				source(14, "Four"),
				source(15, "Five"),
			]);
			for (const id of [11, 12, 13, 14]) {
				const start = crypto.randomUUID();
				await env.JOB_DB.prepare("INSERT INTO generation_starts(id,day,created_at) VALUES(?,?,?)")
					.bind(start, "2020-01-01", "2020-01-01T00:00:00Z")
					.run();
				await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at)
				VALUES(?,?,?,?,?,?,?,?)`)
					.bind(
						crypto.randomUUID(),
						`gutenberg:${id}`,
						"1234567890abcdef",
						start,
						id === 14 ? state : "queued",
						"queued",
						"2020-01-01T00:00:00Z",
						"2020-01-01T00:00:00Z",
					)
					.run();
			}
			const failed = await env.JOB_DB.prepare(
				"SELECT id FROM generation_jobs WHERE source_id='gutenberg:14'",
			).first<{ id: string }>();
			expect((await create(15)).status).toBe(429);
			expect(
				(await request(`/generation-jobs/${failed!.id}/retry`, "POST", undefined, ownerToken))
					.status,
			).toBe(429);
			const today = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
				.bind(new Date().toISOString().slice(0, 10))
				.first<{ n: number }>();
			expect(today?.n).toBe(0);
		},
	);

	it("reclaims an expired lease once while preserving its build", async () => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string }>();
		const first = (
			await (await claim()).json<{ job: { lease_token: string; build_id: string } | null }>()
		).job!;
		await env.JOB_DB.prepare("UPDATE generation_jobs SET lease_until=? WHERE id=?")
			.bind("2020-01-01T00:00:00Z", made.id)
			.run();
		const second = (
			await (
				await claim()
			).json<{ job: { lease_token: string; build_id: string; attempts: number } | null }>()
		).job!;
		expect(second.lease_token).not.toBe(first.lease_token);
		expect(second.build_id).toBe(first.build_id);
		expect(second.attempts).toBe(2);
		const count = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(new Date().toISOString().slice(0, 10))
			.first<{ n: number }>();
		expect(count?.n).toBe(2);
	});

	it("leases one job, rejects wrong lease, and verifies every R2 artifact before completion", async () => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string }>();
		const claimed = await (await claim()).json<{
			job: { id: string; lease_token: string; build_id: string } | null;
		}>();
		const job = claimed.job!;
		expect(job.id).toBe(made.id);
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
		expect(
			(
				await request(
					`/internal/generation-jobs/${job.id}/heartbeat`,
					"POST",
					{ lease_token: crypto.randomUUID() },
					pcToken,
				)
			).status,
		).toBe(409);
		expect(
			(
				await request(
					`/internal/generation-jobs/${job.id}/heartbeat`,
					"POST",
					{ lease_token: job.lease_token },
					pcToken,
				)
			).status,
		).toBe(200);
		const done = {
			lease_token: job.lease_token,
			success: true,
			author_slug: "lewis-carroll",
			title_slug: "alice-g11",
		};
		expect(
			(await request(`/internal/generation-jobs/${job.id}/finish`, "POST", done, pcToken)).status,
		).toBe(409);
		const prefix = `books/lewis-carroll/alice-g11/audio/kokoro-af-heart/builds/${job.build_id}`;
		await env.R2_BUCKET.put(
			"books/lewis-carroll/alice-g11/manifest.json",
			JSON.stringify({
				title: "Alice",
				author: "Lewis Carroll",
				source: "gutenberg",
				renditions: { "kokoro-af-heart": { current_build: job.build_id } },
			}),
		);
		await env.R2_BUCKET.put(
			`${prefix}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build: job.build_id,
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		await env.R2_BUCKET.put(`${prefix}/section_data.json`, "{}");
		await env.R2_BUCKET.put(
			"catalog.json",
			JSON.stringify({
				books: [
					{ author_slug: "lewis-carroll", title_slug: "alice-g11", current_build: job.build_id },
				],
			}),
		);
		expect(
			(await request(`/internal/generation-jobs/${job.id}/finish`, "POST", done, pcToken)).status,
		).toBe(409);
		await env.R2_BUCKET.put(`${prefix}/section-01.m4a`, "audio");
		const finished = await request(
			`/internal/generation-jobs/${job.id}/finish`,
			"POST",
			done,
			pcToken,
		);
		expect(finished.status).toBe(200);
		expect((await finished.json<{ state: string }>()).state).toBe("completed");
		expect((await request("/source-books?q=alice")).status).toBe(200);
		expect(
			(await request(`/internal/generation-jobs/${job.id}/finish`, "POST", done, pcToken)).status,
		).toBe(409);
	});

	it("verifies an expressive rendition while the catalog still defaults to standard audio", async () => {
		await sync([source(11, "Alice")]);
		const made = await (
			await request(
				"/generation-jobs",
				"POST",
				{ source_id: "gutenberg:11", mode: "expressive" },
				ownerToken,
			)
		).json<{ id: string }>();
		const job = (
			await (await claim(true)).json<{ job: { build_id: string; lease_token: string } | null }>()
		).job!;
		const prefix = `books/lewis-carroll/alice-g11/audio/chatterbox-af-heart/builds/${job.build_id}`;
		await env.R2_BUCKET.put(
			"books/lewis-carroll/alice-g11/manifest.json",
			JSON.stringify({
				title: "Alice",
				author: "Lewis Carroll",
				source: "gutenberg",
				renditions: {
					"kokoro-af-heart": { current_build: "1111111111111111" },
					"chatterbox-af-heart": { current_build: job.build_id },
				},
			}),
		);
		await env.R2_BUCKET.put(
			`${prefix}/rendition-manifest.json`,
			JSON.stringify({
				version: 2,
				build: job.build_id,
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		await env.R2_BUCKET.put(
			"books/lewis-carroll/alice-g11/audio/kokoro-af-heart/builds/1111111111111111/rendition-manifest.json",
			JSON.stringify({
				version: 2,
				build: "1111111111111111",
				total_duration_seconds: 12,
				sections: [{ sequence: 1 }],
			}),
		);
		await env.R2_BUCKET.put(`${prefix}/section_data.json`, "{}");
		await env.R2_BUCKET.put(`${prefix}/section-01.m4a`, "audio");
		await env.R2_BUCKET.put(
			"catalog.json",
			JSON.stringify({
				books: [
					{
						author_slug: "lewis-carroll",
						title_slug: "alice-g11",
						current_build: "1111111111111111",
					},
				],
			}),
		);
		const finished = await request(
			`/internal/generation-jobs/${made.id}/finish`,
			"POST",
			{
				lease_token: job.lease_token,
				success: true,
				author_slug: "lewis-carroll",
				title_slug: "alice-g11",
			},
			pcToken,
		);
		expect(finished.status).toBe(200);
		expect(await finished.json<{ mode: string; state: string }>()).toMatchObject({
			mode: "expressive",
			state: "completed",
		});
	});

	it("exposes nonsensitive job status without owner auth and keeps responses uncached", async () => {
		await sync([source(11, "Alice")]);
		const created = await create(11);
		const job = await created.json<{ id: string }>();
		expect(Object.keys(job)).not.toContain("build_id");
		expect(Object.keys(job)).not.toContain("attempts");
		const status = await request(`/generation-jobs/${job.id}`);
		expect(status.status).toBe(200);
		expect(status.headers.get("Cache-Control")).toBe("no-store");
		const body = await status.json<Record<string, unknown>>();
		expect(body).not.toHaveProperty("build_id");
		expect(body).not.toHaveProperty("attempts");
		await env.JOB_DB.prepare(
			"UPDATE generation_jobs SET state='failed',stage='failed',error_code='SensitiveInternalError' WHERE id=?",
		)
			.bind(job.id)
			.run();
		const failed = await (await request(`/generation-jobs/${job.id}`)).json<{
			error_code: string;
		}>();
		expect(failed.error_code).toBe("GENERATION_FAILED");
		searchAllowed = false;
		expect((await request(`/generation-jobs/${job.id}`)).status).toBe(429);
	});
});
