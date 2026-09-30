import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";

const ownerToken = "test-owner-token-longer-than-twenty-four";
const pcToken = "test-consumer-token-longer-than-twenty-four";
let searchAllowed = true;
const limiter = { limit: async () => ({ success: searchAllowed }) } as RateLimit;
const bindings: Env = {
	R2_BUCKET: env.R2_BUCKET,
	JOB_DB: env.JOB_DB,
	OWNER_TOKEN: ownerToken,
	PC_TOKEN: pcToken,
	SEARCH_RATE_LIMITER: limiter,
	AUTH_RATE_LIMITER: limiter,
};
const request = (path: string, method = "GET", body?: unknown, token?: string) =>
	app.request(
		`/api/v1${path}`,
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
const claim = async () => request("/internal/generation-jobs/claim", "POST", undefined, pcToken);

beforeEach(async () => {
	searchAllowed = true;
	await env.JOB_DB.exec(
		"DELETE FROM generation_jobs; DELETE FROM generation_starts; DELETE FROM source_tokens; DELETE FROM source_books;",
	);
});

describe("source search and generation API", () => {
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

	it("keeps autocomplete bounded and uses the token index for candidate lookup", async () => {
		for (let page = 0; page < 4; page++) {
			const batch = Array.from({ length: 50 }, (_, n) =>
				source(page * 50 + n + 1, `Alice story ${page * 50 + n}`),
			);
			expect((await sync(batch)).status).toBe(200);
		}
		const response = await request("/source-books?q=ali&limit=10");
		expect(response.status).toBe(200);
		expect((await response.json<{ books: unknown[] }>()).books).toHaveLength(10);
		const plan = await env.JOB_DB.prepare(
			"EXPLAIN QUERY PLAN SELECT source_id FROM source_tokens WHERE token >= ? AND token < ? LIMIT 80",
		)
			.bind("al", "al\uffff")
			.all<{ detail: string }>();
		expect(plan.results.some((row) => row.detail.includes("COVERING INDEX"))).toBe(true);
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

	it("deduplicates concurrently and enforces the daily GPU cap", async () => {
		await sync([source(11, "Alice"), source(12, "Book Two"), source(13, "Book Three")]);
		const pair = await Promise.all([create(11), create(11)]);
		expect(pair.map((r) => r.status)).toEqual([200, 200]);
		const ids = await Promise.all(pair.map(async (r) => (await r.json<{ id: string }>()).id));
		expect(ids[0]).toBe(ids[1]);
		expect((await create(12)).status).toBe(200);
		expect((await create(13)).status).toBe(429);
		const count = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_jobs").first<{
			n: number;
		}>();
		expect(count?.n).toBe(2);
	});

	it("does not spend a daily slot for a full queue or rejected retry", async () => {
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
					id === 14 ? "failed" : "queued",
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
		const made = await (await create(11)).json<{ id: string; build_id: string }>();
		const first = (await (await claim()).json<{ job: { lease_token: string } | null }>()).job!;
		await env.JOB_DB.prepare("UPDATE generation_jobs SET lease_until=? WHERE id=?")
			.bind("2020-01-01T00:00:00Z", made.id)
			.run();
		const second = (
			await (
				await claim()
			).json<{ job: { lease_token: string; build_id: string; attempts: number } | null }>()
		).job!;
		expect(second.lease_token).not.toBe(first.lease_token);
		expect(second.build_id).toBe(made.build_id);
		expect(second.attempts).toBe(2);
		const count = await env.JOB_DB.prepare("SELECT COUNT(*) n FROM generation_starts WHERE day=?")
			.bind(new Date().toISOString().slice(0, 10))
			.first<{ n: number }>();
		expect(count?.n).toBe(2);
	});

	it("leases one job, rejects wrong lease, and verifies every R2 artifact before completion", async () => {
		await sync([source(11, "Alice")]);
		const made = await (await create(11)).json<{ id: string; build_id: string }>();
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

	it("does not expose job status without owner auth and keeps responses uncached", async () => {
		await sync([source(11, "Alice")]);
		const job = await (await create(11)).json<{ id: string }>();
		expect((await request(`/generation-jobs/${job.id}`)).status).toBe(401);
		const status = await request(`/generation-jobs/${job.id}`, "GET", undefined, ownerToken);
		expect(status.status).toBe(200);
		expect(status.headers.get("Cache-Control")).toBe("no-store");
	});
});
