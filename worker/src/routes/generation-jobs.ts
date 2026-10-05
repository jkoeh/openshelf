import { createRoute, z } from "@hono/zod-openapi";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { credentialStatus, noStore } from "../utils/job-auth";
import { adminCredentialStatus } from "../utils/google-admin";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const Id = z.object({
	id: z
		.string()
		.uuid()
		.openapi({ param: { name: "id", in: "path" } }),
});
const Job = z
	.object({
		id: z.string(),
		source_id: z.string(),
		mode: z.enum(["standard", "expressive"]),
		state: z.enum(["queued", "running", "completed", "failed", "canceled"]),
		stage: z.string(),
		author_slug: z.string().nullable(),
		title_slug: z.string().nullable(),
		error_code: z.enum(["RIGHTS_NOT_VERIFIED", "BOOK_TOO_LONG", "GENERATION_FAILED"]).nullable(),
		created_at: z.string(),
		updated_at: z.string(),
	})
	.openapi("GenerationJob");
const ManagedJob = Job.extend({
	title: z.string(),
	author: z.string(),
	priority: z.number().int().min(0).max(1),
	attempts: z.number().int(),
	lease_until: z.string().nullable(),
}).openapi("ManagedGenerationJob");
const ManagedList = z.object({
	active: z.array(ManagedJob),
	recent: z.array(ManagedJob),
});
const Priority = z.object({ priority: z.enum(["normal", "high"]) });
const Create = z.object({
	source_id: z.string().regex(/^gutenberg:[1-9][0-9]*$/),
	mode: z.enum(["standard", "expressive"]).default("standard"),
	regenerate: z.boolean().default(false),
});
const Claim = z.object({ expressive: z.boolean().default(false) });
const Lease = z.object({ lease_token: z.string().uuid() });
const Progress = Lease.extend({
	stage: z.enum(["download", "parse", "direction", "synthesis", "alignment", "encode", "upload"]),
});
const Finish = Lease.extend({
	success: z.boolean(),
	author_slug: z
		.string()
		.regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/)
		.optional(),
	title_slug: z
		.string()
		.regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/)
		.optional(),
	error_code: z.string().max(60).optional(),
});
const ClaimJob = Job.extend({
	error_code: z.string().nullable(),
	build_id: z.string(),
	attempts: z.number().int(),
	epub_url: z.string().url(),
	title: z.string(),
	author: z.string(),
	lease_token: z.string(),
});
const ClaimResponse = z.object({ job: ClaimJob.nullable() });
const Ok = z.object({ ok: z.literal(true) });
const error = { description: "Error", content: { "application/json": { schema: ErrorSchema } } };
const jobResponse = { description: "Job", content: { "application/json": { schema: Job } } };
const create = createRoute({
	method: "post",
	path: "/",
	tags: ["generation"],
	summary: "Request a capped fixed-voice generation job",
	request: { body: { content: { "application/json": { schema: Create } } } },
	responses: {
		200: jobResponse,
		400: error,
		401: error,
		404: error,
		409: error,
		429: error,
		503: error,
	},
});
const status = createRoute({
	method: "get",
	path: "/:id",
	tags: ["generation"],
	summary: "Read generation job status",
	request: { params: Id },
	responses: { 200: jobResponse, 400: error, 404: error, 429: error, 503: error },
});
const cancel = createRoute({
	method: "post",
	path: "/:id/cancel",
	tags: ["generation"],
	summary: "Owner cancels queued or running work",
	request: { params: Id },
	responses: {
		200: jobResponse,
		400: error,
		401: error,
		404: error,
		409: error,
		429: error,
		503: error,
	},
});
const retry = createRoute({
	method: "post",
	path: "/:id/retry",
	tags: ["generation"],
	summary: "Retry failed job in its original build",
	request: { params: Id },
	responses: {
		200: jobResponse,
		400: error,
		401: error,
		404: error,
		409: error,
		429: error,
		503: error,
	},
});
const listManaged = createRoute({
	method: "get",
	path: "/",
	tags: ["generation-owner"],
	summary: "List active and recent generation jobs for the owner",
	responses: {
		200: { description: "Bounded owner queue", content: { "application/json": { schema: ManagedList } } },
		401: error,
		429: error,
		503: error,
	},
});
const setPriority = createRoute({
	method: "post",
	path: "/:id/priority",
	tags: ["generation-owner"],
	summary: "Set queued job priority",
	request: { params: Id, body: { content: { "application/json": { schema: Priority } } } },
	responses: {
		200: { description: "Updated job", content: { "application/json": { schema: ManagedJob } } },
		400: error,
		401: error,
		404: error,
		409: error,
		429: error,
		503: error,
	},
});
const claim = createRoute({
	method: "post",
	path: "/claim",
	tags: ["generation-internal"],
	summary: "PC claims one job",
	request: { body: { content: { "application/json": { schema: Claim } } } },
	responses: {
		200: {
			description: "Leased job or no work",
			content: { "application/json": { schema: ClaimResponse } },
		},
		401: error,
		429: error,
		503: error,
	},
});
const heartbeat = createRoute({
	method: "post",
	path: "/:id/heartbeat",
	tags: ["generation-internal"],
	summary: "Renew PC lease",
	request: { params: Id, body: { content: { "application/json": { schema: Lease } } } },
	responses: {
		200: { description: "Renewed", content: { "application/json": { schema: Ok } } },
		400: error,
		401: error,
		409: error,
		429: error,
		503: error,
	},
});
const progress = createRoute({
	method: "post",
	path: "/:id/progress",
	tags: ["generation-internal"],
	summary: "Report PC stage",
	request: { params: Id, body: { content: { "application/json": { schema: Progress } } } },
	responses: {
		200: { description: "Updated", content: { "application/json": { schema: Ok } } },
		400: error,
		401: error,
		409: error,
		429: error,
		503: error,
	},
});
const finish = createRoute({
	method: "post",
	path: "/:id/finish",
	tags: ["generation-internal"],
	summary: "Verify and finish PC job",
	request: { params: Id, body: { content: { "application/json": { schema: Finish } } } },
	responses: { 200: jobResponse, 400: error, 401: error, 409: error, 429: error, 503: error },
});

interface Row {
	id: string;
	source_id: string;
	mode: "standard" | "expressive";
	build_id: string;
	state: "queued" | "running" | "completed" | "failed" | "canceled";
	stage: string;
	attempts: number;
	author_slug: string | null;
	title_slug: string | null;
	error_code: string | null;
	created_at: string;
	updated_at: string;
}
interface ManagedRow extends Row {
	title: string;
	author: string;
	priority: number;
	lease_until: string | null;
}
function publicJob(row: Row): z.infer<typeof Job> {
	const error_code = row.error_code === "RightsNotVerified"
		? "RIGHTS_NOT_VERIFIED" as const
		: row.error_code === "BookTooLong"
			? "BOOK_TOO_LONG" as const
			: row.error_code
				? "GENERATION_FAILED" as const
				: null;
	return {
		id: row.id,
		source_id: row.source_id,
		mode: row.mode,
		state: row.state,
		stage: row.stage,
		author_slug: row.author_slug,
		title_slug: row.title_slug,
		error_code,
		created_at: row.created_at,
		updated_at: row.updated_at,
	};
}
const fields =
	"id,source_id,mode,build_id,state,stage,attempts,author_slug,title_slug,error_code,created_at,updated_at";
const managedFields = `${fields.split(",").map((field) => `j.${field}`).join(",")},j.priority,j.lease_until,b.title,b.author`;
function managedJob(row: ManagedRow): z.infer<typeof ManagedJob> {
	return {
		...publicJob(row),
		title: row.title,
		author: row.author,
		priority: row.priority,
		attempts: row.attempts,
		lease_until: row.lease_until,
	};
}
const now = () => new Date().toISOString();
const slug = (value: string) =>
	value
		.toLowerCase()
		.trim()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-|-$/g, "");
async function get(db: D1Database, id: string) {
	return db.prepare(`SELECT ${fields} FROM generation_jobs WHERE id=?`).bind(id).first<Row>();
}
async function getManaged(db: D1Database, id: string) {
	return db.prepare(`SELECT ${managedFields} FROM generation_jobs j JOIN source_books b ON b.source_id=j.source_id WHERE j.id=?`)
		.bind(id).first<ManagedRow>();
}
async function auth(req: Request, env: Env, pc = false) {
	return pc ? credentialStatus(req, env, env.PC_TOKEN) : adminCredentialStatus(req, env);
}
function ready(env: Env) {
	return (
		!!env.JOB_DB &&
		!!env.OWNER_TOKEN &&
		!!env.PC_TOKEN &&
		!!env.AUTH_RATE_LIMITER &&
		!!env.SEARCH_RATE_LIMITER &&
		!!env.CREATE_RATE_LIMITER
	);
}
const reservation = (
	db: D1Database,
	id: string,
	timestamp: string,
	condition = "1=1",
	values: string[] = [],
) =>
	db
		.prepare(`INSERT INTO generation_starts(id,day,created_at)
	SELECT ?,?,? WHERE (SELECT COUNT(*) FROM generation_starts WHERE day=?) < 2 AND ${condition}`)
		.bind(id, timestamp.slice(0, 10), timestamp, timestamp.slice(0, 10), ...values);

async function verifyPublished(
	env: Env,
	row: Row,
	author: string,
	title: string,
): Promise<boolean> {
	const selectedRendition = row.mode === "expressive" ? "chatterbox-af-heart" : "kokoro-af-heart";
	const manifest = await env.R2_BUCKET.get(r2Key.bookManifest(author, title));
	if (!manifest) return false;
	const book = (await manifest.json()) as {
		source?: string;
		renditions?: Record<string, { current_build?: string }>;
	};
	if (book.source !== "gutenberg") return false;
	if (book.renditions?.[selectedRendition]?.current_build !== row.build_id) return false;
	const rendition = await env.R2_BUCKET.get(
		r2Key.renditionManifest(author, title, selectedRendition, row.build_id),
	);
	if (!rendition) return false;
	const data = (await rendition.json()) as {
		version?: number;
		build?: string;
		sections?: { sequence: number }[];
	};
	if (
		data.version !== 2 ||
		data.build !== row.build_id ||
		!Array.isArray(data.sections) ||
		data.sections.length === 0
	)
		return false;
	if (
		!(await env.R2_BUCKET.head(r2Key.sectionData(author, title, selectedRendition, row.build_id)))
	)
		return false;
	for (const section of data.sections)
		if (
			!Number.isInteger(section.sequence) ||
			section.sequence < 1 ||
			!(await env.R2_BUCKET.head(
				r2Key.audio(author, title, selectedRendition, row.build_id, section.sequence),
			))
		)
			return false;
	const catalog = await env.R2_BUCKET.get("catalog.json");
	if (!catalog) return false;
	const listing = (await catalog.json()) as {
		books?: { author_slug: string; title_slug: string; current_build: string }[];
	};
	return !!listing.books?.some(
		(b) => b.author_slug === author && b.title_slug === title &&
			(row.mode === "expressive" || b.current_build === row.build_id),
	);
}

const owner = createOpenAPIApp<{ Bindings: Env }>();
owner.openapi(create, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (!(await c.env.CREATE_RATE_LIMITER!.limit({ key: `create:${ip}` })).success)
		return c.json({ error: { code: "RATE_LIMITED", message: "Try again later" } }, 429);
	const { source_id, regenerate, mode } = c.req.valid("json");
	const authStatus = mode === "expressive" || regenerate || c.req.header("Authorization")
		? await auth(c.req.raw, c.env)
		: 0;
	if (authStatus)
		return c.json(
			{
				error: {
					code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			authStatus,
		);
	const db = c.env.JOB_DB!;
	const source = await db
		.prepare("SELECT source_id,author_slug,title_slug FROM source_books WHERE source_id=?")
		.bind(source_id)
		.first<{ source_id: string; author_slug: string | null; title_slug: string | null }>();
	if (!source)
		return c.json({ error: { code: "NOT_FOUND", message: "Source edition not indexed" } }, 404);
	const existing = await db
		.prepare(
			`SELECT ${fields} FROM generation_jobs WHERE source_id=? AND state IN ('queued','running') LIMIT 1`,
		)
		.bind(source_id)
		.first<Row>();
	if (existing) return existing.mode === mode
		? c.json(publicJob(existing), 200, noStore)
		: c.json({ error: { code: "ACTIVE_JOB", message: "This edition already has an active job" } }, 409);
	if (source.author_slug && !regenerate)
		return c.json(
			{ error: { code: "ALREADY_AVAILABLE", message: "This edition is already available" } },
			409,
		);
	const timestamp = now(),
		id = crypto.randomUUID(),
		start = crypto.randomUUID(),
		build = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
	try {
		await db.batch([
			reservation(
				db,
				start,
				timestamp,
				"(SELECT COUNT(*) FROM generation_jobs WHERE state='queued') < 3 AND NOT EXISTS(SELECT 1 FROM generation_jobs WHERE source_id=? AND state IN ('queued','running'))",
				[source_id],
			),
			db
				.prepare(`INSERT INTO generation_jobs(id,source_id,mode,build_id,start_id,state,stage,created_at,updated_at)
			SELECT ?,?,?,?,?,'queued','queued',?,? WHERE EXISTS(SELECT 1 FROM generation_starts WHERE id=?)
			AND (SELECT COUNT(*) FROM generation_jobs WHERE state='queued') < 3`)
				.bind(id, source_id, mode, build, start, timestamp, timestamp, start),
		]);
	} catch {
		const duplicate = await db
			.prepare(
				`SELECT ${fields} FROM generation_jobs WHERE source_id=? AND state IN ('queued','running') LIMIT 1`,
			)
			.bind(source_id)
			.first<Row>();
		if (duplicate) return duplicate.mode === mode
			? c.json(publicJob(duplicate), 200, noStore)
			: c.json({ error: { code: "ACTIVE_JOB", message: "This edition already has an active job" } }, 409);
		return c.json({ error: { code: "LIMIT_REACHED", message: "Generation quota reached" } }, 429);
	}
	const job = await get(db, id);
	if (!job) {
		const duplicate = await db
			.prepare(
				`SELECT ${fields} FROM generation_jobs WHERE source_id=? AND state IN ('queued','running') LIMIT 1`,
			)
			.bind(source_id)
			.first<Row>();
		if (duplicate) return duplicate.mode === mode
			? c.json(publicJob(duplicate), 200, noStore)
			: c.json({ error: { code: "ACTIVE_JOB", message: "This edition already has an active job" } }, 409);
		return c.json({ error: { code: "LIMIT_REACHED", message: "Generation quota reached" } }, 429);
	}
	return c.json(publicJob(job), 200, noStore);
});
owner.openapi(status, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (!(await c.env.SEARCH_RATE_LIMITER!.limit({ key: `job-status:${ip}` })).success)
		return c.json({ error: { code: "RATE_LIMITED", message: "Try again later" } }, 429);
	const job = await get(c.env.JOB_DB!, c.req.valid("param").id);
	return job
		? c.json(publicJob(job), 200, noStore)
		: c.json({ error: { code: "NOT_FOUND", message: "Job not found" } }, 404);
});
owner.openapi(cancel, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env);
	if (authStatus)
		return c.json(
			{ error: { code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED", message: "Invalid credential or too many attempts" } },
			authStatus,
		);
	const db = c.env.JOB_DB!, id = c.req.valid("param").id;
	const existing = await get(db, id);
	if (!existing) return c.json({ error: { code: "NOT_FOUND", message: "Job not found" } }, 404);
	if (existing.state === "canceled") return c.json(publicJob(existing), 200, noStore);
	if (existing.state !== "queued" && existing.state !== "running")
		return c.json({ error: { code: "NOT_CANCELABLE", message: "Job has already finished" } }, 409);
	await db.prepare(`UPDATE generation_jobs SET state='canceled',stage='canceled',lease_token=NULL,lease_until=NULL,updated_at=?
		WHERE id=? AND state IN ('queued','running')`).bind(now(), id).run();
	const updated = await get(db, id);
	return updated?.state === "canceled"
		? c.json(publicJob(updated), 200, noStore)
		: c.json({ error: { code: "NOT_CANCELABLE", message: "Job has already finished" } }, 409);
});
owner.openapi(retry, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env);
	if (authStatus)
		return c.json(
			{
				error: {
					code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			authStatus,
		);
	const db = c.env.JOB_DB!,
		id = c.req.valid("param").id,
		job = await get(db, id);
	if (!job) return c.json({ error: { code: "NOT_FOUND", message: "Job not found" } }, 404);
	if (job.state !== "failed" || job.attempts >= 3)
		return c.json({ error: { code: "NOT_RETRYABLE", message: "Job cannot be retried" } }, 409);
	const start = crypto.randomUUID(),
		timestamp = now();
	await db.batch([
		reservation(
			db,
			start,
			timestamp,
			"(SELECT COUNT(*) FROM generation_jobs WHERE state='queued') < 3 AND EXISTS(SELECT 1 FROM generation_jobs WHERE id=? AND state='failed' AND attempts<3)",
			[id],
		),
		db
			.prepare(`UPDATE generation_jobs SET state='queued',stage='queued',start_id=?,error_code=NULL,updated_at=?
		WHERE id=? AND state='failed' AND EXISTS(SELECT 1 FROM generation_starts WHERE id=?)
		AND (SELECT COUNT(*) FROM generation_jobs WHERE state='queued') < 3`)
			.bind(start, timestamp, id, start),
	]);
	const updated = await get(db, id);
	return updated?.state === "queued"
		? c.json(publicJob(updated), 200, noStore)
		: c.json({ error: { code: "LIMIT_REACHED", message: "Generation quota reached" } }, 429);
});

const management = createOpenAPIApp<{ Bindings: Env }>();
management.openapi(listManaged, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env);
	if (authStatus)
		return c.json({ error: { code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED", message: "Invalid credential or too many attempts" } }, authStatus);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (!(await c.env.SEARCH_RATE_LIMITER!.limit({ key: `owner-queue:${ip}` })).success)
		return c.json({ error: { code: "RATE_LIMITED", message: "Try again later" } }, 429);
	const db = c.env.JOB_DB!;
	const [active, recent] = await Promise.all([
		db.prepare(`SELECT ${managedFields} FROM generation_jobs j JOIN source_books b ON b.source_id=j.source_id
			WHERE j.state IN ('queued','running')
			ORDER BY CASE WHEN j.state='running' THEN 0 ELSE 1 END,j.priority DESC,j.created_at LIMIT 20`)
			.all<ManagedRow>(),
		db.prepare(`SELECT ${managedFields} FROM generation_jobs j JOIN source_books b ON b.source_id=j.source_id
			WHERE j.state IN ('completed','failed','canceled') ORDER BY j.updated_at DESC LIMIT 20`)
			.all<ManagedRow>(),
	]);
	return c.json({ active: active.results.map(managedJob), recent: recent.results.map(managedJob) }, 200, noStore);
});
management.openapi(setPriority, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env);
	if (authStatus)
		return c.json({ error: { code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED", message: "Invalid credential or too many attempts" } }, authStatus);
	const db = c.env.JOB_DB!, id = c.req.valid("param").id;
	const existing = await getManaged(db, id);
	if (!existing) return c.json({ error: { code: "NOT_FOUND", message: "Job not found" } }, 404);
	if (existing.state !== "queued")
		return c.json({ error: { code: "NOT_QUEUED", message: "Only queued jobs can be prioritized" } }, 409);
	const priority = c.req.valid("json").priority === "high" ? 1 : 0;
	const result = await db.prepare("UPDATE generation_jobs SET priority=?,updated_at=? WHERE id=? AND state='queued'")
		.bind(priority, now(), id).run();
	if (!result.meta.changes)
		return c.json({ error: { code: "NOT_QUEUED", message: "Only queued jobs can be prioritized" } }, 409);
	return c.json(managedJob((await getManaged(db, id))!), 200, noStore);
});

const internal = createOpenAPIApp<{ Bindings: Env }>();
internal.openapi(claim, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env, true);
	if (authStatus)
		return c.json(
			{
				error: {
					code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			authStatus,
		);
	const db = c.env.JOB_DB!,
		timestamp = now();
	const { expressive } = c.req.valid("json");
	await db
		.prepare(`UPDATE generation_jobs SET state='failed',stage='failed',error_code='LEASE_EXHAUSTED',lease_token=NULL,lease_until=NULL,updated_at=?
		WHERE state='running' AND lease_until<? AND attempts>=3`)
		.bind(timestamp, timestamp)
		.run();
	const candidate = await db
		.prepare(`SELECT id,state FROM generation_jobs WHERE (state='queued' OR (state='running' AND lease_until<? AND attempts<3))
		AND (mode='standard' OR ?=1)
		ORDER BY CASE WHEN state='queued' THEN 0 ELSE 1 END,priority DESC,created_at LIMIT 1`)
		.bind(timestamp, expressive ? 1 : 0)
		.first<{ id: string; state: string }>();
	if (!candidate) return c.json({ job: null }, 200, noStore);
	const lease = crypto.randomUUID(),
		expiry = new Date(Date.now() + 120000).toISOString(),
		start = crypto.randomUUID();
	if (candidate.state === "running") {
		await db.batch([
			reservation(
				db,
				start,
				timestamp,
				"EXISTS(SELECT 1 FROM generation_jobs WHERE id=? AND state='running' AND lease_until<? AND attempts<3)",
				[candidate.id, timestamp],
			),
			db
				.prepare(`UPDATE generation_jobs SET state='running',stage='download',attempts=attempts+1,start_id=?,lease_token=?,lease_until=?,updated_at=?
			WHERE id=? AND state='running' AND lease_until<? AND attempts<3 AND EXISTS(SELECT 1 FROM generation_starts WHERE id=?)`)
				.bind(start, lease, expiry, timestamp, candidate.id, timestamp, start),
		]);
	} else {
		await db
			.prepare(`UPDATE generation_jobs SET state='running',stage='download',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=?
			WHERE id=? AND state='queued' AND attempts<3`)
			.bind(lease, expiry, timestamp, candidate.id)
			.run();
	}
	const job = await db
		.prepare(
			`SELECT j.${fields.replaceAll(",", ",j.")},b.epub_url,b.title,b.author FROM generation_jobs j JOIN source_books b ON b.source_id=j.source_id WHERE j.id=? AND j.lease_token=?`,
		)
		.bind(candidate.id, lease)
		.first<Row & { epub_url: string; title: string; author: string }>();
	return c.json({ job: job ? { ...job, lease_token: lease } : null }, 200, noStore);
});
internal.openapi(heartbeat, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env, true);
	if (authStatus)
		return c.json(
			{
				error: {
					code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			authStatus,
		);
	const result = await c.env
		.JOB_DB!.prepare(
			`UPDATE generation_jobs SET lease_until=?,updated_at=? WHERE id=? AND lease_token=? AND state='running' AND lease_until>?`,
		)
		.bind(
			new Date(Date.now() + 120000).toISOString(),
			now(),
			c.req.valid("param").id,
			c.req.valid("json").lease_token,
			now(),
		)
		.run();
	return result.meta.changes
		? c.json({ ok: true as const }, 200, noStore)
		: c.json({ error: { code: "LEASE_LOST", message: "Lease expired" } }, 409);
});
internal.openapi(progress, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env, true);
	if (authStatus)
		return c.json(
			{
				error: {
					code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			authStatus,
		);
	const { lease_token, stage } = c.req.valid("json");
	const result = await c.env
		.JOB_DB!.prepare(
			`UPDATE generation_jobs SET stage=?,updated_at=? WHERE id=? AND lease_token=? AND state='running' AND lease_until>?`,
		)
		.bind(stage, now(), c.req.valid("param").id, lease_token, now())
		.run();
	return result.meta.changes
		? c.json({ ok: true as const }, 200, noStore)
		: c.json({ error: { code: "LEASE_LOST", message: "Lease expired" } }, 409);
});
internal.openapi(finish, async (c) => {
	if (!ready(c.env))
		return c.json({ error: { code: "UNAVAILABLE", message: "Generation is not configured" } }, 503);
	const authStatus = await auth(c.req.raw, c.env, true);
	if (authStatus)
		return c.json(
			{
				error: {
					code: authStatus === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			authStatus,
		);
	const db = c.env.JOB_DB!,
		id = c.req.valid("param").id,
		body = c.req.valid("json");
	const row = await get(db, id);
	const current = await db
		.prepare("SELECT lease_token,lease_until FROM generation_jobs WHERE id=?")
		.bind(id)
		.first<{ lease_token: string; lease_until: string }>();
	if (
		!row ||
		row.state !== "running" ||
		current?.lease_token !== body.lease_token ||
		current.lease_until <= now()
	)
		return c.json({ error: { code: "LEASE_LOST", message: "Lease expired" } }, 409);
	if (body.success) {
		const source = await db
			.prepare("SELECT title,author FROM source_books WHERE source_id=?")
			.bind(row.source_id)
			.first<{ title: string; author: string }>();
		if (
			!source ||
			body.author_slug !== (slug(source.author) || "unknown") ||
			body.title_slug !== `${slug(source.title) || "untitled"}-g${row.source_id.slice(10)}`
		)
			return c.json(
				{
					error: { code: "WRONG_EDITION", message: "Published book does not match source edition" },
				},
				409,
			);
	}
	if (
		body.success &&
		(!body.author_slug ||
			!body.title_slug ||
			!(await verifyPublished(c.env, row, body.author_slug, body.title_slug)))
	)
		return c.json(
			{ error: { code: "NOT_PUBLISHED", message: "Build is not fully published" } },
			409,
		);
	const timestamp = now(),
		state = body.success ? "completed" : "failed";
	const update = await db
		.prepare(`UPDATE generation_jobs SET state=?,stage=?,author_slug=?,title_slug=?,error_code=?,lease_token=NULL,lease_until=NULL,updated_at=?
		WHERE id=? AND state='running' AND lease_token=? AND lease_until>?`)
		.bind(
			state,
			state,
			body.author_slug ?? null,
			body.title_slug ?? null,
			body.success ? null : (body.error_code ?? "PIPELINE_FAILED"),
			timestamp,
			id,
			body.lease_token,
			timestamp,
		)
		.run();
	if (!update.meta.changes)
		return c.json({ error: { code: "LEASE_LOST", message: "Lease expired" } }, 409);
	if (body.success)
		await db
			.prepare("UPDATE source_books SET author_slug=?,title_slug=? WHERE source_id=?")
			.bind(body.author_slug!, body.title_slug!, row.source_id)
			.run();
	return c.json(publicJob((await get(db, id))!), 200, noStore);
});
export { owner, management, internal };
