import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_SHORT } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { adminCredentialStatus } from "../utils/google-admin";
import { noStore } from "../utils/job-auth";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({
		param: { name: "author", in: "path" },
		example: "franz-kafka",
	}),
	title: SlugSchema.openapi({
		param: { name: "title", in: "path" },
		example: "the-trial",
	}),
});

const ManifestSectionSchema = z.object({
	sequence: z.number().int().positive(),
	section_type: z.string(),
	ordinal: z.number().int().positive().nullable(),
	display_label: z.string(),
	display_title: z.string(),
	filename: z.string(),
	duration_seconds: z.number(),
	word_count: z.number().int(),
});

const BookManifestRenditionSchema = z.object({
	voice: z.string(),
	engine: z.string(),
	display: z.string(),
	current_build: z.string(),
	available_builds: z.array(z.string()),
});

const BookManifestSchema = z.object({
	title: z.string(),
	author: z.string(),
	source: z.string(),
	renditions: z.record(BookManifestRenditionSchema),
});

const RenditionManifestSchema = z.object({
	version: z.literal(2),
	build: z.string(),
	rendition: z.string(),
	voice: z.string(),
	engine: z.string(),
	pipeline_version: z.string(),
	total_duration_seconds: z.number(),
	section_count: z.number().int().nonnegative(),
	sections: z.array(ManifestSectionSchema),
});

const MergedRenditionSchema = BookManifestRenditionSchema.extend({
	total_duration_seconds: z.number(),
	sections: z.array(ManifestSectionSchema),
});

const ManifestSchema = z
	.object({
		title: z.string(),
		author: z.string(),
		source: z.string(),
		renditions: z.record(MergedRenditionSchema),
	})
	.openapi("Manifest");

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["books"],
	summary: "Get book manifest",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "Book manifest with typed section list and durations",
			content: { "application/json": { schema: ManifestSchema } },
		},
		400: {
			description: "Invalid slug",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Book not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

const managementSchema = z
	.object({
		source_id: z.string().nullable(),
		active_job_id: z.string().nullable(),
		deleting: z.boolean(),
	})
	.openapi("BookManagement");
const managementError = {
	description: "Owner action failed",
	content: { "application/json": { schema: ErrorSchema } },
};
const managementRoute = createRoute({
	method: "get",
	path: "/management",
	tags: ["books-owner"],
	summary: "Resolve owner actions for a published book",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "Book owner actions",
			content: { "application/json": { schema: managementSchema } },
		},
		400: managementError,
		401: managementError,
		404: managementError,
		429: managementError,
		503: managementError,
	},
});
const deleteRoute = createRoute({
	method: "delete",
	path: "/",
	tags: ["books-owner"],
	summary: "Delete all published artifacts for a book",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "Book deleted",
			content: {
				"application/json": { schema: z.object({ deleted: z.literal(true) }) },
			},
		},
		400: managementError,
		401: managementError,
		409: managementError,
		429: managementError,
		503: managementError,
	},
});

app.openapi(route, async (c) => {
	const { author, title } = c.req.valid("param");

	const obj = await c.env.R2_BUCKET.get(r2Key.bookManifest(author, title));
	if (!obj) {
		return c.json(
			{
				error: {
					code: "NOT_FOUND",
					message: `Book not found: ${author}/${title}`,
				},
			},
			404,
		);
	}

	const bookManifest = BookManifestSchema.parse(await obj.json());
	const renditions: z.infer<typeof ManifestSchema>["renditions"] = {};

	for (const [rendition, entry] of Object.entries(bookManifest.renditions)) {
		const renditionManifestObj = await c.env.R2_BUCKET.get(
			r2Key.renditionManifest(author, title, rendition, entry.current_build),
		);
		if (!renditionManifestObj) {
			return c.json(
				{
					error: {
						code: "NOT_FOUND",
						message: `Build manifest not found: ${author}/${title}/${rendition}/${entry.current_build}`,
					},
				},
				404,
			);
		}

		const parsed = RenditionManifestSchema.safeParse(
			await renditionManifestObj.json(),
		);
		if (!parsed.success) {
			return c.json(
				{
					error: {
						code: "NOT_FOUND",
						message: `Incompatible audiobook build: ${author}/${title}/${rendition}/${entry.current_build}`,
					},
				},
				404,
			);
		}
		const renditionManifest = parsed.data;
		renditions[rendition] = {
			...entry,
			total_duration_seconds: renditionManifest.total_duration_seconds,
			sections: renditionManifest.sections,
		};
	}

	return c.json(
		{
			title: bookManifest.title,
			author: bookManifest.author,
			source: bookManifest.source,
			renditions,
		},
		200,
		{ "Cache-Control": CACHE_SHORT },
	);
});

app.openapi(managementRoute, async (c) => {
	const auth = await adminCredentialStatus(c.req.raw, c.env);
	if (auth)
		return c.json(
			{
				error: {
					code: auth === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Sign in as the owner",
				},
			},
			auth,
			noStore,
		);
	if (!c.env.JOB_DB || !c.env.SEARCH_RATE_LIMITER)
		return c.json(
			{
				error: {
					code: "UNAVAILABLE",
					message: "Book management is not configured",
				},
			},
			503,
			noStore,
		);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (
		!(await c.env.SEARCH_RATE_LIMITER.limit({ key: `book-management:${ip}` }))
			.success
	)
		return c.json(
			{ error: { code: "RATE_LIMITED", message: "Try again later" } },
			429,
			noStore,
		);
	const { author, title } = c.req.valid("param");
	const db = c.env.JOB_DB;
	const deleting = await db
		.prepare(
			"SELECT 1 FROM book_deletions WHERE author_slug=? AND title_slug=?",
		)
		.bind(author, title)
		.first();
	if (
		!deleting &&
		!(await c.env.R2_BUCKET.head(r2Key.bookManifest(author, title)))
	)
		return c.json(
			{ error: { code: "NOT_FOUND", message: "Book not found" } },
			404,
			noStore,
		);
	const source = await db
		.prepare(
			"SELECT source_id FROM source_books WHERE author_slug=? AND title_slug=? LIMIT 1",
		)
		.bind(author, title)
		.first<{ source_id: string }>();
	const active = source
		? await db
				.prepare(
					"SELECT id FROM generation_jobs WHERE source_id=? AND state IN ('queued','running') LIMIT 1",
				)
				.bind(source.source_id)
				.first<{ id: string }>()
		: null;
	return c.json(
		{
			source_id: source?.source_id ?? null,
			active_job_id: active?.id ?? null,
			deleting: !!deleting,
		},
		200,
		noStore,
	);
});

async function removeCatalogEntry(
	bucket: R2Bucket,
	author: string,
	title: string,
) {
	// Conditional writes preserve another publisher's concurrent catalog update.
	for (let attempt = 0; attempt < 5; attempt++) {
		const object = await bucket.get("catalog.json");
		if (!object) return;
		const catalog = await object.json<{
			books: { author_slug: string; title_slug: string }[];
			generated_at: string;
		}>();
		if (!Array.isArray(catalog.books)) throw new Error("Invalid catalog");
		const books = catalog.books.filter(
			(book) => book.author_slug !== author || book.title_slug !== title,
		);
		if (books.length === catalog.books.length) return;
		const written = await bucket.put(
			"catalog.json",
			JSON.stringify({
				...catalog,
				books,
				generated_at: new Date().toISOString(),
			}),
			{
				onlyIf: { etagMatches: object.etag },
				httpMetadata: object.httpMetadata,
			},
		);
		if (written) return;
	}
	throw new Error("Catalog changed during deletion");
}

app.openapi(deleteRoute, async (c) => {
	const auth = await adminCredentialStatus(c.req.raw, c.env);
	if (auth)
		return c.json(
			{
				error: {
					code: auth === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Sign in as the owner",
				},
			},
			auth,
			noStore,
		);
	if (!c.env.JOB_DB || !c.env.SEARCH_RATE_LIMITER)
		return c.json(
			{
				error: {
					code: "UNAVAILABLE",
					message: "Book management is not configured",
				},
			},
			503,
			noStore,
		);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (
		!(await c.env.SEARCH_RATE_LIMITER.limit({ key: `book-delete:${ip}` }))
			.success
	)
		return c.json(
			{ error: { code: "RATE_LIMITED", message: "Try again later" } },
			429,
			noStore,
		);
	const { author, title } = c.req.valid("param");
	const db = c.env.JOB_DB;
	const bucket = c.env.R2_BUCKET;
	const token = crypto.randomUUID();
	const timestamp = new Date().toISOString();
	const canceledBefore = new Date(Date.now() - 120_000).toISOString();
	const expires = new Date(Date.now() + 600_000).toISOString();
	const locked = await db
		.prepare(`INSERT INTO book_deletions(author_slug,title_slug,lease_token,lease_until)
		SELECT ?,?,?,? WHERE NOT EXISTS (
			SELECT 1 FROM generation_jobs j JOIN source_books s ON s.source_id=j.source_id
			WHERE s.author_slug=? AND s.title_slug=? AND (
				j.state IN ('queued','running') OR (j.state='canceled' AND j.attempts>0 AND j.updated_at>?)
			)
		) ON CONFLICT(author_slug,title_slug) DO UPDATE SET lease_token=excluded.lease_token,lease_until=excluded.lease_until
		WHERE book_deletions.lease_until<=?`)
		.bind(author, title, token, expires, author, title, canceledBefore, timestamp)
		.run();
	if (!locked.meta.changes)
		return c.json(
			{
				error: {
					code: "BOOK_BUSY",
					message:
						"Cancel or finish active generation, then allow up to two minutes for canceled work to stop. If deletion is already running, wait for it to finish and try again.",
				},
			},
			409,
			noStore,
		);
	try {
		const manifestKey = r2Key.bookManifest(author, title);
		const prefix = `${r2Key.bookPrefix(author, title)}/`;
		await bucket.delete(manifestKey);
		// Re-list from the start after each deletion; a cursor can skip removed keys.
		let empty = false;
		for (let batch = 0; batch < 20; batch++) {
			const owned = await db
				.prepare(
					"SELECT 1 FROM book_deletions WHERE author_slug=? AND title_slug=? AND lease_token=? AND lease_until>?",
				)
				.bind(author, title, token, new Date().toISOString())
				.first();
			if (!owned) throw new Error("Deletion lease lost");
			const listed = await bucket.list({ prefix, limit: 1000 });
			const keys = listed.objects.map((object) => object.key);
			if (!keys.length && !listed.truncated) {
				empty = true;
				break;
			}
			if (keys.length) await bucket.delete(keys);
		}
		if (!empty) throw new Error("Deletion batch limit reached");
		await removeCatalogEntry(bucket, author, title);
		await db.batch([
			db.prepare("DELETE FROM published_books WHERE author_slug=? AND title_slug=?").bind(author, title),
			db
				.prepare(`UPDATE generation_jobs SET state='canceled',stage='deleted',attempts=3,author_slug=NULL,title_slug=NULL,updated_at=?
				WHERE source_id IN (SELECT source_id FROM source_books WHERE author_slug=? AND title_slug=?)`)
				.bind(new Date().toISOString(), author, title),
			db
				.prepare(
					"UPDATE source_books SET author_slug=NULL,title_slug=NULL WHERE author_slug=? AND title_slug=?",
				)
				.bind(author, title),
			db
				.prepare(
					"DELETE FROM book_deletions WHERE author_slug=? AND title_slug=? AND lease_token=?",
				)
				.bind(author, title, token),
		]);
		return c.json({ deleted: true as const }, 200, noStore);
	} catch {
		// Retain the generation guard, but let an owner retry interrupted cleanup.
		await db
			.prepare(
				"UPDATE book_deletions SET lease_until=? WHERE author_slug=? AND title_slug=? AND lease_token=?",
			)
			.bind(new Date().toISOString(), author, title, token)
			.run();
		return c.json(
			{
				error: {
					code: "DELETE_INCOMPLETE",
					message:
						"Deletion did not finish. Retry Delete book to complete cleanup.",
				},
			},
			503,
			noStore,
		);
	}
});

export default app;
