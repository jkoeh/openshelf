import { env, fetchMock } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";

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
const sync = async (books: ReturnType<typeof source>[]) =>
	request("/internal/source-books/sync", "POST", { books }, pcToken);
const create = async (id: number, token = ownerToken) =>
	request("/generation-jobs", "POST", { source_id: `gutenberg:${id}` }, token);
const claim = async (expressive = false) =>
	request("/internal/generation-jobs/claim", "POST", { expressive }, pcToken);

beforeEach(async () => {
	searchAllowed = true;
	createAllowed = true;
	origin = `https://${crypto.randomUUID()}.example`;
	await env.JOB_DB.exec(
		"DELETE FROM generation_jobs; DELETE FROM generation_starts; DELETE FROM source_tokens; DELETE FROM source_books;",
	);
});

describe("source search and generation API", () => {
	it("streams source EPUB for immediate reading and validates upstream redirects", async () => {
		await sync([source(11, "Alice")]);
		fetchMock.activate();
		fetchMock.disableNetConnect();
		try {
			const upstream = fetchMock.get("https://www.gutenberg.org");
			upstream.intercept({ path: "/ebooks/11.epub3.images" }).reply(302, "", { headers: { Location: "/cache/epub/11/pg11-images-3.epub" } });
			upstream.intercept({ path: "/cache/epub/11/pg11-images-3.epub" }).reply(200, "epub-bytes");
			const response = await request("/source-books/gutenberg%3A11/epub?inline=1");
			expect(response.status).toBe(200);
			expect(response.headers.get("Content-Type")).toBe("application/epub+zip");
			expect(await response.text()).toBe("epub-bytes");
			upstream.intercept({ path: "/ebooks/11.epub3.images" }).reply(302, "", { headers: { Location: "https://evil.example/book.epub" } });
			expect((await request("/source-books/gutenberg%3A11/epub?inline=1")).status).toBe(502);
			fetchMock.assertNoPendingInterceptors();
		} finally { fetchMock.deactivate(); }
	});
	it("downloads an indexed EPUB independently of failed audio, PC credentials, and quotas", async () => {
		await sync([source(2554, "Crime and Punishment")]);
		const made = await (await create(2554)).json<{ id: string }>();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed',error_code='BookTooLong' WHERE id=?")
			.bind(made.id).run();
		const searched = await (await request("/source-books?q=punishment"))
			.json<{ books: { job_error_code: string }[] }>();
		expect(searched.books[0].job_error_code).toBe("BOOK_TOO_LONG");
		createAllowed = false;
		const response = await app.request(`${origin}/api/v1/source-books/gutenberg%3A2554/epub`, {}, {
			R2_BUCKET: env.R2_BUCKET, JOB_DB: env.JOB_DB, SEARCH_RATE_LIMITER: limiter,
		});
		expect(response.status).toBe(302);
		expect(response.headers.get("Location")).toBe(source(2554, "Crime").epub_url);
		expect(response.headers.get("Cache-Control")).toBe("no-store");
		const job = await (await request(`/generation-jobs/${made.id}`)).json<{ state: string }>();
		expect(job.state).toBe("failed");
		expect(await env.JOB_DB.prepare("SELECT COUNT(*) count FROM generation_starts").first("count")).toBe(1);
	});

	it("validates and rate limits source EPUB lookups without redirecting invalid editions", async () => {
		await sync([source(2554, "Crime and Punishment")]);
		const path = "/source-books/gutenberg%3A2554/epub";
		expect((await request("/source-books/gutenberg%3A999999/epub")).status).toBe(404);
		expect((await request("/source-books/not-a-source/epub")).status).toBe(400);
		for (const epubUrl of [
			"not-a-url", "https://evil.example/ebooks/2554.epub",
			"https://www.gutenberg.org/ebooks/11.epub3.images",
			"https://www.gutenberg.org/ebooks/2554.html",
			"https://user:password@www.gutenberg.org/ebooks/2554.epub3.images",
			"https://www.gutenberg.org/ebooks/2554.epub3.images?redirect=evil",
			"https://www.gutenberg.org/ebooks/2554.epub3.images#fragment",
			"http://www.gutenberg.org/ebooks/2554.epub3.images",
		]) {
			await env.JOB_DB.prepare("UPDATE source_books SET epub_url=? WHERE source_id='gutenberg:2554'").bind(epubUrl).run();
			const response = await request(path);
			expect(response.status).toBe(400);
			expect(response.headers.get("Location")).toBeNull();
		}
		await env.JOB_DB.prepare("UPDATE source_books SET epub_url='https://www.gutenberg.org/cache/epub/2554/pg2554-images-3.epub' WHERE source_id='gutenberg:2554'").run();
		expect((await request(path)).status).toBe(302);
		searchAllowed = false;
		expect((await request(path)).status).toBe(429);
		expect((await app.request(`${origin}/api/v1${path}`, {}, { R2_BUCKET: env.R2_BUCKET })).status).toBe(503);
	});

	it("shows a private bounded queue and claims high-priority queued work first", async () => {
		await sync([source(11, "Alice"), source(12, "A Second Book")]);
		const first = await (await create(11)).json<{ id: string }>();
		const second = await (await create(12)).json<{ id: string }>();
		const path = "/admin/generation-jobs";
		expect((await request(path)).status).toBe(401);
		expect((await request(path, "GET", undefined, pcToken)).status).toBe(401);
		expect((await request(`${path}/${second.id}/priority`, "POST", { priority: "urgent" }, ownerToken)).status).toBe(400);
		const prioritized = await request(`${path}/${second.id}/priority`, "POST", { priority: "high" }, ownerToken);
		expect(prioritized.status).toBe(200);
		expect((await prioritized.json<{ priority: number }>()).priority).toBe(1);
		const listed = await request(path, "GET", undefined, ownerToken);
		expect(listed.status).toBe(200);
		expect(listed.headers.get("Cache-Control")).toBe("no-store");
		const body = await listed.json<{ active: { id: string; title: string; priority: number; state: string; lease_until: string | null }[]; recent: unknown[] }>();
		expect(body.active.map((job) => job.id)).toEqual([second.id, first.id]);
		expect(body.active[0]).toMatchObject({ title: "A Second Book", priority: 1, state: "queued", lease_until: null });
		expect(body.recent).toEqual([]);
		expect(JSON.stringify(body)).not.toContain("lease_token");
		expect(JSON.stringify(body)).not.toContain("build_id");
		expect(JSON.stringify(body)).not.toContain("epub_url");
		const claimed = await (await claim()).json<{ job: { id: string } }>();
		expect(claimed.job.id).toBe(second.id);
		expect((await request(`${path}/${second.id}/priority`, "POST", { priority: "normal" }, ownerToken)).status).toBe(409);
		const running = await (await request(path, "GET", undefined, ownerToken)).json<{ active: { id: string; lease_until: string | null }[] }>();
		expect(running.active[0].id).toBe(second.id);
		expect(running.active[0].lease_until).toBeTruthy();
		expect((await request(`/generation-jobs/${second.id}/cancel`, "POST", undefined, ownerToken)).status).toBe(200);
		const after = await (await request(path, "GET", undefined, ownerToken)).json<{ recent: { id: string; state: string }[] }>();
		expect(after.recent[0]).toMatchObject({ id: second.id, state: "canceled" });
		searchAllowed = false;
		expect((await request(path, "GET", undefined, ownerToken)).status).toBe(429);
	});
	it("bounds owner history to the twenty newest terminal jobs", async () => {
		await sync([source(11, "Alice")]);
		await env.JOB_DB.prepare("INSERT INTO generation_starts(id,day,created_at) VALUES('fixture-start','2026-10-05','2026-10-05T00:00:00Z')").run();
		const ids: string[] = [];
		for (let index = 0; index < 25; index++) {
			const id = crypto.randomUUID();
			ids.push(id);
			await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at)
				VALUES(?,'gutenberg:11','1234567890abcdef','fixture-start','completed','completed',?,?)`)
				.bind(id, new Date(Date.UTC(2026, 9, 5, 0, index)).toISOString(),
					new Date(Date.UTC(2026, 9, 5, 0, index)).toISOString()).run();
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
		await env.JOB_DB.prepare("UPDATE source_books SET author_slug='lewis-carroll',title_slug='alice-g11' WHERE source_id='gutenberg:11'").run();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(made.id).run();
		const result = await request("/source-books?q=alice");
		const body = await result.json<{ books: { state: string; job_state: string; job_id: string; job_updated_at: string }[] }>();
		expect(body.books[0]).toMatchObject({ state: "available", job_state: "failed", job_id: made.id });
		expect(body.books[0].job_updated_at).toBeTruthy();
	});

	it("returns the newest job ID and state after another request for the same edition", async () => {
		await sync([source(11, "Alice")]);
		const first = await (await create(11)).json<{ id: string }>();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(first.id).run();
		const second = await (await create(11)).json<{ id: string }>();
		expect(second.id).not.toBe(first.id);
		const result = await request("/source-books?q=alice");
		const body = await result.json<{ books: { job_id: string; job_state: string }[] }>();
		expect(body.books[0]).toMatchObject({ job_id: second.id, job_state: "queued" });
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed' WHERE id=?")
			.bind(second.id).run();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='queued',stage='queued' WHERE id=?")
			.bind(first.id).run();
		origin = `https://${crypto.randomUUID()}.example`;
		const active = await (await request("/source-books?q=alice"))
			.json<{ books: { job_id: string; job_state: string }[] }>();
		expect(active.books[0]).toMatchObject({ job_id: first.id, job_state: "queued" });
		const activePlan = await env.JOB_DB.prepare(
			"EXPLAIN QUERY PLAN SELECT id FROM generation_jobs WHERE source_id=? AND state IN ('queued','running') LIMIT 1",
		).bind("gutenberg:11").all<{ detail: string }>();
		expect(activePlan.results.some((row) => row.detail.includes("one_active_generation"))).toBe(true);
		const latestPlan = await env.JOB_DB.prepare(
			"EXPLAIN QUERY PLAN SELECT id FROM generation_jobs WHERE source_id=? ORDER BY created_at DESC LIMIT 1",
		).bind("gutenberg:11").all<{ detail: string }>();
		expect(latestPlan.results.some((row) => row.detail.includes("latest_source_job"))).toBe(true);
		expect([...activePlan.results, ...latestPlan.results]
			.some((row) => row.detail.includes("USE TEMP B-TREE"))).toBe(false);
	});
	it("keeps browser admin identity closed when Google is unconfigured or invalid", async () => {
		expect((await request("/admin/me", "GET", undefined, ownerToken)).status).toBe(503);
		const configured = { ...bindings, GOOGLE_CLIENT_ID: "openshelf-test.apps.googleusercontent.com" };
		const invalid = await app.request(`${origin}/api/v1/admin/me`, {
			headers: { Authorization: "Bearer forged.google.token" },
		}, configured);
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

	it("briefly caches identical public suggestions without bypassing rate limits", async () => {
		await sync([source(71, "Zephyr Atlas")]);
		const first = await request("/source-books?q=Zephyr&limit=1");
		expect(first.status).toBe(200);
		expect(first.headers.get("Cache-Control")).toBe("no-store");
		expect((await first.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:71",
		);
		await env.JOB_DB.exec("DELETE FROM source_tokens; DELETE FROM source_books;");
		const cached = await request("/source-books?q=zephyr&limit=1");
		expect(cached.headers.get("Cache-Control")).toBe("no-store");
		expect((await cached.json<{ books: { source_id: string }[] }>()).books[0].source_id).toBe(
			"gutenberg:71",
		);
		searchAllowed = false;
		expect((await request("/source-books?q=zephyr&limit=1")).status).toBe(429);
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
		await sync([source(11, "Alice"), source(12, "Book Two"), source(13, "Book Three"), source(14, "Book Four")]);
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
		await env.JOB_DB.batch(Array.from({ length: 299 }, () => env.JOB_DB.prepare(
			"INSERT INTO generation_starts(id,day,created_at) VALUES(?,?,?)",
		).bind(crypto.randomUUID(), day, new Date().toISOString())));
		const results = await Promise.all([create(11), create(12)]);
		expect(results.map(result => result.status).sort()).toEqual([200, 429]);
		const used = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(day).first<{ n: number }>();
		expect(used?.n).toBe(300);
	});

	it("allows capped public requests but keeps regeneration owner-only", async () => {
		await sync([source(11, "Alice")]);
		createAllowed = false;
		expect((await request("/generation-jobs", "POST", { source_id: "gutenberg:11" })).status).toBe(429);
		const before = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first<{ n: number }>();
		expect(before?.n).toBe(0);
		createAllowed = true;
		const response = await request("/generation-jobs", "POST", { source_id: "gutenberg:11" });
		expect(response.status).toBe(200);
		expect((await response.json<{ state: string }>()).state).toBe("queued");
		expect((await request("/generation-jobs", "POST", { source_id: "gutenberg:11", regenerate: true })).status).toBe(401);
	});

	it("requires owner auth for expressive jobs and leases them only to a capable PC", async () => {
		await sync([source(11, "Alice")]);
		const requested = { source_id: "gutenberg:11", mode: "expressive" };
		expect((await request("/generation-jobs", "POST", requested)).status).toBe(401);
		expect((await request("/generation-jobs", "POST", requested, "wrong-token")).status).toBe(401);
		expect((await request("/generation-jobs", "POST", { ...requested, mode: "custom" }, ownerToken)).status).toBe(400);
		const made = await request("/generation-jobs", "POST", requested, ownerToken);
		expect(made.status).toBe(200);
		const job = await made.json<{ id: string; mode: string }>();
		expect(job.mode).toBe("expressive");
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
		const current = await (await request("/source-books?q=alice"))
			.json<{ books: { job_mode: string; job_id: string }[] }>();
		expect(current.books[0]).toMatchObject({ job_mode: "expressive", job_id: job.id });
		expect((await request("/generation-jobs", "POST", { source_id: "gutenberg:11" })).status).toBe(409);
		const claimed = await (await claim(true))
			.json<{ job: { id: string; mode: string; build_id: string } | null }>();
		expect(claimed.job).toMatchObject({ id: job.id, mode: "expressive" });
		const starts = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(new Date().toISOString().slice(0, 10)).first<{ n: number }>();
		expect(starts?.n).toBe(1);
	});

	it("cancels queued work without refund, and prevents a claim", async () => {
		await sync([source(11, "Alice")]);
		const job = await (await request("/generation-jobs", "POST", { source_id: "gutenberg:11" })).json<{ id: string }>();
		const path = `/generation-jobs/${job.id}/cancel`;
		expect((await request(path, "POST")).status).toBe(401);
		const canceled = await request(path, "POST", undefined, ownerToken);
		expect(canceled.status).toBe(200);
		expect((await canceled.json<{ state: string }>()).state).toBe("canceled");
		expect((await request(path, "POST", undefined, ownerToken)).status).toBe(200);
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
		const starts = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first<{ n: number }>();
		expect(starts?.n).toBe(1);
	});

	it("revokes a running lease on cancellation", async () => {
		await sync([source(11, "Alice")]);
		const job = await (await create(11)).json<{ id: string }>();
		const running = (await (await claim()).json<{ job: { lease_token: string } | null }>()).job!;
		expect((await request(`/generation-jobs/${job.id}/cancel`, "POST", undefined, ownerToken)).status).toBe(200);
		expect((await request(`/internal/generation-jobs/${job.id}/heartbeat`, "POST", { lease_token: running.lease_token }, pcToken)).status).toBe(409);
		expect((await (await claim()).json<{ job: unknown }>()).job).toBeNull();
	});

	it.each(["failed", "canceled"])("retries %s jobs with the same build and a fresh lease", async (state) => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string }>();
		const first = (await (await claim()).json<{ job: { build_id: string; lease_token: string } }>()).job;
		if (state === "canceled") {
			expect((await request(`/generation-jobs/${made.id}/cancel`, "POST", undefined, ownerToken)).status).toBe(200);
		} else {
			expect((await request(`/internal/generation-jobs/${made.id}/finish`, "POST", {
				lease_token: first.lease_token, success: false, error_code: "PIPELINE_FAILED",
			}, pcToken)).status).toBe(200);
		}
		const path = `/generation-jobs/${made.id}/retry`;
		expect((await request(path, "POST")).status).toBe(401);
		const retried = await request(path, "POST", undefined, ownerToken);
		expect(retried.status).toBe(200);
		expect(await retried.json()).toMatchObject({ id: made.id, state: "queued", stage: "queued", error_code: null });
		expect((await request(path, "POST", undefined, ownerToken)).status).toBe(409);
		const next = (await (await claim()).json<{ job: { id: string; build_id: string; lease_token: string; attempts: number } }>()).job;
		expect(next).toMatchObject({ id: made.id, build_id: first.build_id, attempts: 2 });
		expect(next.lease_token).not.toBe(first.lease_token);
		expect((await request(`/internal/generation-jobs/${made.id}/heartbeat`, "POST", { lease_token: first.lease_token }, pcToken)).status).toBe(409);
		expect(await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first()).toMatchObject({ n: 2 });
	});

	it.each(["failed", "canceled"])("rejects %s retry after three attempts without spending a start", async (state) => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string }>();
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state=?,attempts=3 WHERE id=?").bind(state, made.id).run();
		expect((await request(`/generation-jobs/${made.id}/retry`, "POST", undefined, ownerToken)).status).toBe(409);
		expect(await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts").first()).toMatchObject({ n: 1 });
	});

	it.each(["failed", "canceled"])("does not spend a daily slot for a full queue or rejected %s retry", async (state) => {
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
			(await request(`/generation-jobs/${failed!.id}/retry`, "POST", undefined, ownerToken)).status,
		).toBe(429);
		const today = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(new Date().toISOString().slice(0, 10))
			.first<{ n: number }>();
		expect(today?.n).toBe(0);
	});

	it("reclaims an expired lease once while preserving its build", async () => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string }>();
		const first = (await (await claim()).json<{ job: { lease_token: string; build_id: string } | null }>()).job!;
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
				source: "gutenberg",
				renditions: { "kokoro-af-heart": { current_build: job.build_id } },
			}),
		);
		await env.R2_BUCKET.put(
			`${prefix}/rendition-manifest.json`,
			JSON.stringify({ version: 2, build: job.build_id, sections: [{ sequence: 1 }] }),
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
		const made = await (await request("/generation-jobs", "POST",
			{ source_id: "gutenberg:11", mode: "expressive" }, ownerToken))
			.json<{ id: string }>();
		const job = (await (await claim(true))
			.json<{ job: { build_id: string; lease_token: string } | null }>()).job!;
		const prefix = `books/lewis-carroll/alice-g11/audio/chatterbox-af-heart/builds/${job.build_id}`;
		await env.R2_BUCKET.put("books/lewis-carroll/alice-g11/manifest.json", JSON.stringify({
			source: "gutenberg",
			renditions: {
				"kokoro-af-heart": { current_build: "previous-standard-build" },
				"chatterbox-af-heart": { current_build: job.build_id },
			},
		}));
		await env.R2_BUCKET.put(`${prefix}/rendition-manifest.json`,
			JSON.stringify({ version: 2, build: job.build_id, sections: [{ sequence: 1 }] }));
		await env.R2_BUCKET.put(`${prefix}/section_data.json`, "{}");
		await env.R2_BUCKET.put(`${prefix}/section-01.m4a`, "audio");
		await env.R2_BUCKET.put("catalog.json", JSON.stringify({ books: [{
			author_slug: "lewis-carroll", title_slug: "alice-g11", current_build: "previous-standard-build",
		}] }));
		const finished = await request(`/internal/generation-jobs/${made.id}/finish`, "POST", {
			lease_token: job.lease_token, success: true,
			author_slug: "lewis-carroll", title_slug: "alice-g11",
		}, pcToken);
		expect(finished.status).toBe(200);
		expect((await finished.json<{ mode: string; state: string }>())).toMatchObject({
			mode: "expressive", state: "completed",
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
		await env.JOB_DB.prepare("UPDATE generation_jobs SET state='failed',stage='failed',error_code='SensitiveInternalError' WHERE id=?").bind(job.id).run();
		const failed = await (await request(`/generation-jobs/${job.id}`)).json<{ error_code: string }>();
		expect(failed.error_code).toBe("GENERATION_FAILED");
		searchAllowed = false;
		expect((await request(`/generation-jobs/${job.id}`)).status).toBe(429);
	});
});
