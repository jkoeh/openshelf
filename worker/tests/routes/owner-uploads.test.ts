import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";

const ownerToken = "owner-upload-token-longer-than-twenty-four";
const pcToken = "pc-upload-token-longer-than-twenty-four";
const limiter = { limit: async () => ({ success: true }) } as RateLimit;
const bindings: Env = {
	R2_BUCKET: env.R2_BUCKET,
	JOB_DB: env.JOB_DB,
	OWNER_TOKEN: ownerToken,
	PC_TOKEN: pcToken,
	AUTH_RATE_LIMITER: limiter,
	SEARCH_RATE_LIMITER: limiter,
	CREATE_RATE_LIMITER: limiter,
};
const json = (path: string, body?: object, token?: string) =>
	app.request(
		`https://test.example/api/v1${path}`,
		{
			method: body ? "POST" : "GET",
			headers: {
				...(body ? { "Content-Type": "application/json" } : {}),
				...(token ? { Authorization: `Bearer ${token}` } : {}),
			},
			...(body ? { body: JSON.stringify(body) } : {}),
		},
		bindings,
	);
const upload = (body = "紅樓夢，Hello 世界！", token = ownerToken, extra = "") =>
	app.request(
		`https://test.example/api/v1/admin/uploads?title=Owner%20document&author=Owner&language=zh&format=txt&publish=yes${extra}`,
		{
			method: "POST",
			body,
			headers: { "Content-Type": "application/octet-stream", Authorization: `Bearer ${token}` },
		},
		bindings,
	);
beforeEach(async () => {
	await env.JOB_DB.exec(
		"DELETE FROM generation_jobs; DELETE FROM generation_starts; DELETE FROM source_tokens; DELETE FROM owner_uploads; DELETE FROM source_books;",
	);
});
describe("owner document boundary", () => {
	it("retains private originals after retirement and lets the owner start fresh audio", async () => {
		const document = await (await upload("Retained original text")).json<{
			id: string;
			source_id: string;
		}>();
		const retired = await (
			await json("/generation-jobs", { source_id: document.source_id }, ownerToken)
		).json<{ id: string }>();
		// Publication deletion/pruning clears pointers and retires its existing jobs.
		await env.JOB_DB.prepare(
			"UPDATE generation_jobs SET state='failed',stage='deleted',attempts=3 WHERE id=?",
		)
			.bind(retired.id)
			.run();
		await env.JOB_DB.prepare(
			"UPDATE source_books SET author_slug=NULL,title_slug=NULL WHERE source_id=?",
		)
			.bind(document.source_id)
			.run();
		const listing = await (await json("/admin/uploads", undefined, ownerToken)).json<{
			uploads: { id: string; job_id: string | null }[];
		}>();
		expect(listing.uploads).toHaveLength(1);
		expect(listing.uploads[0]).toMatchObject({ id: document.id, job_id: null });
		const original = await json(`/internal/uploads/${document.id}`, undefined, pcToken);
		expect(original.status).toBe(200);
		expect(await original.text()).toBe("Retained original text");
		expect((await json(`/internal/uploads/${document.id}`)).status).toBe(401);
		const fresh = await json("/generation-jobs", { source_id: document.source_id }, ownerToken);
		expect(fresh.status).toBe(200);
		const job = await fresh.json<{ id: string; state: string }>();
		expect(job.id).not.toBe(retired.id);
		expect(job.state).toBe("queued");
		const updated = await (await json("/admin/uploads", undefined, ownerToken)).json<{
			uploads: { job_id: string | null }[];
		}>();
		expect(updated.uploads[0].job_id).toBe(job.id);
		const audit = await env.JOB_DB.prepare("SELECT stage,attempts FROM generation_jobs WHERE id=?")
			.bind(retired.id)
			.first();
		expect(audit).toMatchObject({ stage: "deleted", attempts: 3 });
	});
	it("unready configured English runtime leaves English jobs queued", async () => {
		const sync = await json(
			"/internal/source-books/sync",
			{
				books: [
					{
						source_id: "gutenberg:11",
						title: "Alice",
						author: "Lewis Carroll",
						epub_url: "https://www.gutenberg.org/ebooks/11.epub3.images",
					},
				],
			},
			pcToken,
		);
		expect(sync.status).toBe(200);
		await json("/generation-jobs", { source_id: "gutenberg:11" });
		expect(
			await (
				await json(
					"/internal/generation-jobs/claim",
					{ english: false, mandarin: true, documents: true },
					pcToken,
				)
			).json(),
		).toEqual({ job: null });
		const result = await (
			await json("/internal/generation-jobs/claim", { english: true }, pcToken)
		).json<{ job: { source_id: string } }>();
		expect(result.job.source_id).toBe("gutenberg:11");
	});
	it("targeted PC claim never steals another queued document or bypasses capability checks", async () => {
		const first = await (await upload("First")).json<{ source_id: string }>();
		const second = await (await upload("Second")).json<{ source_id: string }>();
		const one = await (
			await json("/generation-jobs", { source_id: first.source_id }, ownerToken)
		).json<{ id: string }>();
		const two = await (
			await json("/generation-jobs", { source_id: second.source_id }, ownerToken)
		).json<{ id: string }>();
		expect(
			await (
				await json("/internal/generation-jobs/claim", { job_id: two.id, mandarin: true }, pcToken)
			).json(),
		).toEqual({ job: null });
		const result = await (
			await json(
				"/internal/generation-jobs/claim",
				{ job_id: two.id, mandarin: true, documents: true },
				pcToken,
			)
		).json<{ job: { id: string } }>();
		expect(result.job.id).toBe(two.id);
		expect(
			await (
				await json(
					"/internal/generation-jobs/claim",
					{ job_id: crypto.randomUUID(), mandarin: true, documents: true },
					pcToken,
				)
			).json(),
		).toEqual({ job: null });
		const status = await (await json(`/generation-jobs/${one.id}`, undefined, ownerToken)).json<{
			state: string;
		}>();
		expect(status.state).toBe("queued");
	});
	it("keeps originals and queued status private, routes Mandarin and requires a capable PC", async () => {
		expect((await upload("private", "wrong-token")).status).toBe(401);
		const response = await upload();
		expect(response.status).toBe(201);
		const document = await response.json<{ id: string; source_id: string }>();
		expect((await json("/admin/uploads")).status).toBe(401);
		expect((await json("/admin/uploads", undefined, ownerToken)).status).toBe(200);
		expect(
			(await json(`/source-books/${encodeURIComponent(document.source_id)}/epub?inline=1`)).status,
		).toBe(400);
		const found = await (await json("/source-books?q=Owner")).json<{ books: unknown[] }>();
		expect(found.books).toEqual([]);
		expect((await json(`/internal/uploads/${document.id}`)).status).toBe(401);
		const raw = await json(`/internal/uploads/${document.id}`, undefined, pcToken);
		expect(await raw.text()).toBe("紅樓夢，Hello 世界！");
		expect(raw.headers.get("Cache-Control")).toBe("no-store");
		expect((await json("/generation-jobs", { source_id: document.source_id })).status).toBe(401);
		const created = await json("/generation-jobs", { source_id: document.source_id }, ownerToken);
		expect(created.status).toBe(200);
		const job = await created.json<{ id: string }>();
		expect((await json(`/generation-jobs/${job.id}`)).status).toBe(401);
		expect((await json(`/generation-jobs/${job.id}`, undefined, ownerToken)).status).toBe(200);
		expect(
			await (await json("/internal/generation-jobs/claim", { mandarin: true }, pcToken)).json(),
		).toEqual({ job: null });
		expect(
			await (await json("/internal/generation-jobs/claim", { documents: true }, pcToken)).json(),
		).toEqual({ job: null });
		const claim = await (
			await json("/internal/generation-jobs/claim", { documents: true, mandarin: true }, pcToken)
		).json<{
			job: { source_id: string; language: string; provenance: { rights_acknowledged: boolean } };
		}>();
		expect(claim.job.source_id).toBe(document.source_id);
		expect(claim.job.language).toBe("zh");
		expect(claim.job.provenance.rights_acknowledged).toBe(true);
	});
	it("rejects malformed/unsupported uploads, missing acknowledgement and oversized bodies", async () => {
		for (const body of ["", "\0binary", "  "]) expect((await upload(body)).status).toBe(400);
		const request = (query: string, body: string, length?: number) =>
			app.request(
				`https://test.example/api/v1/admin/uploads?title=T&author=A&language=en&${query}`,
				{
					method: "POST",
					body,
					headers: {
						Authorization: `Bearer ${ownerToken}`,
						...(length ? { "Content-Length": String(length) } : {}),
					},
				},
				bindings,
			);
		expect((await request("format=doc&publish=yes", "binary")).status).toBe(400);
		expect((await request("format=txt", "text")).status).toBe(400);
		expect((await request("format=pdf&publish=yes", "html")).status).toBe(400);
		expect((await request("format=txt&publish=yes", "x", 17 * 1024 * 1024)).status).toBe(413);
	});
});
