import { createRoute, z } from "@hono/zod-openapi";
import { CATALOG_PAGE_SIZE_DEFAULT, CATALOG_PAGE_SIZE_MAX } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { credentialStatus, noStore } from "../utils/job-auth";
import { createOpenAPIApp } from "../utils/openapi-app";
import { reconcilePublishedBooks, syncPublishedBook } from "../utils/published-catalog";
import { canonicalSearch } from "../utils/source-search";

const CatalogBookSchema = z
	.object({
		author: z.string(),
		author_slug: z.string(),
		title: z.string(),
		title_slug: z.string(),
		source: z.string(),
		rendition: z.string(),
		current_build: z.string(),
		total_duration_seconds: z.number(),
		section_count: z.number().int(),
		has_cover: z.boolean(),
	})
	.openapi("CatalogBook");
const CatalogResponseSchema = z
	.object({
		version: z.number().int().positive(),
		generated_at: z.string(),
		books: z.array(CatalogBookSchema),
		total: z.number().int().nonnegative(),
		page: z.number().int().positive(),
		limit: z.number().int().positive(),
	})
	.openapi("Catalog");
const QuerySchema = z.object({
	q: z.string().max(80).optional().openapi({
		description: "Current D1 title/author substring search, including equivalent Chinese scripts",
	}),
	author: z.string().max(200).optional(),
	page: z.string().optional().openapi({ example: "1" }),
	limit: z.string().optional().openapi({ example: "20" }),
});
const error = { description: "Error", content: { "application/json": { schema: ErrorSchema } } };
const route = createRoute({
	method: "get",
	path: "/",
	tags: ["catalog"],
	summary: "List the current public D1 publication catalog without result caching",
	request: { query: QuerySchema },
	responses: {
		200: {
			description: "Filtered and paginated current publications",
			content: { "application/json": { schema: CatalogResponseSchema } },
		},
		400: error,
		503: error,
	},
});
const syncRoute = createRoute({
	method: "post",
	path: "/sync",
	tags: ["catalog-internal"],
	summary: "PC indexes up to 10 publications from their actual current R2 manifests",
	request: {
		body: {
			content: {
				"application/json": {
					schema: z.object({
						books: z
							.array(z.object({ author_slug: SlugSchema, title_slug: SlugSchema }))
							.min(1)
							.max(10),
					}),
				},
			},
		},
	},
	responses: {
		200: {
			description: "Publication keys processed",
			content: {
				"application/json": { schema: z.object({ synced: z.number().int().nonnegative() }) },
			},
		},
		400: error,
		401: error,
		409: error,
		429: error,
		503: error,
	},
});
const PublicationKey = z.object({ author_slug: SlugSchema, title_slug: SlugSchema });
const reconcileRoute = createRoute({
	method: "post",
	path: "/reconcile",
	tags: ["catalog-internal"],
	summary: "PC reconciles at most 10 publication rows against current R2 manifests",
	request: {
		body: {
			content: { "application/json": { schema: z.object({ after: PublicationKey.optional() }) } },
		},
	},
	responses: {
		200: {
			description: "Publication rows examined",
			content: {
				"application/json": {
					schema: z.object({
						checked: z.number().int().nonnegative(),
						removed: z.number().int().nonnegative(),
						after: PublicationKey.nullable(),
					}),
				},
			},
		},
		400: error,
		401: error,
		429: error,
		503: error,
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();
const internal = createOpenAPIApp<{ Bindings: Env }>();
app.use("*", async (c, next) => {
	c.header("Cache-Control", "no-store");
	await next();
});
internal.use("*", async (c, next) => {
	c.header("Cache-Control", "no-store");
	await next();
});
type Row = Omit<z.infer<typeof CatalogBookSchema>, "has_cover"> & { has_cover: number };

app.openapi(route, async (c) => {
	if (!c.env.JOB_DB)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Publication catalog is not configured" } },
			503,
			noStore,
		);
	const { q, author, page: pageStr, limit: limitStr } = c.req.valid("query");
	const page = Math.min(1_000_000, Math.max(1, Math.floor(Number(pageStr) || 1)));
	const limit = Math.min(
		CATALOG_PAGE_SIZE_MAX,
		Math.max(1, Math.floor(Number(limitStr) || CATALOG_PAGE_SIZE_DEFAULT)),
	);
	const clauses = [
		"NOT EXISTS(SELECT 1 FROM book_deletions d WHERE d.author_slug=b.author_slug AND d.title_slug=b.title_slug)",
	];
	const values: string[] = [];
	if (q) {
		clauses.push("(instr(b.title_search,?)>0 OR instr(b.author_search,?)>0)");
		values.push(canonicalSearch(q), canonicalSearch(q));
	}
	if (author) {
		clauses.push("instr(b.author_search,?)>0");
		values.push(canonicalSearch(author));
	}
	const where = `WHERE ${clauses.join(" AND ")}`;
	const results = await c.env.JOB_DB.batch([
		c.env.JOB_DB.prepare(
			`SELECT author,author_slug,title,title_slug,source,rendition,current_build,total_duration_seconds,section_count,has_cover FROM published_books b ${where} ORDER BY b.author_search,b.title_search,b.author_slug,b.title_slug LIMIT ? OFFSET ?`,
		).bind(...values, limit, (page - 1) * limit),
		c.env.JOB_DB.prepare(`SELECT COUNT(*) total FROM published_books b ${where}`).bind(...values),
	]);
	const books = (results[0].results as unknown as Row[]).map((row) => ({
		...row,
		has_cover: !!row.has_cover,
	}));
	const count = results[1].results as unknown as { total: number }[];
	return c.json(
		{
			version: 2,
			generated_at: new Date().toISOString(),
			books,
			total: Number(count[0]?.total ?? 0),
			page,
			limit,
		},
		200,
		noStore,
	);
});

internal.openapi(syncRoute, async (c) => {
	if (!c.env.JOB_DB)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Publication catalog is not configured" } },
			503,
			noStore,
		);
	const auth = await credentialStatus(c.req.raw, c.env, c.env.PC_TOKEN);
	if (auth)
		return c.json(
			{
				error: {
					code: auth === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "PC credential required",
				},
			},
			auth,
			noStore,
		);
	let synced = 0;
	try {
		for (const book of c.req.valid("json").books)
			if (await syncPublishedBook(c.env, book.author_slug, book.title_slug)) synced++;
	} catch {
		return c.json(
			{
				error: {
					code: "INVALID_PUBLICATION",
					message:
						"A publication has missing or incompatible R2 manifests; repair it and retry indexing",
				},
			},
			409,
			noStore,
		);
	}
	return c.json({ synced }, 200, noStore);
});

internal.openapi(reconcileRoute, async (c) => {
	if (!c.env.JOB_DB)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Publication catalog is not configured" } },
			503,
			noStore,
		);
	const auth = await credentialStatus(c.req.raw, c.env, c.env.PC_TOKEN);
	if (auth)
		return c.json(
			{
				error: {
					code: auth === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "PC credential required",
				},
			},
			auth,
			noStore,
		);
	try {
		return c.json(await reconcilePublishedBooks(c.env, c.req.valid("json").after), 200, noStore);
	} catch {
		return c.json(
			{
				error: {
					code: "STORAGE_UNAVAILABLE",
					message: "Catalog reconciliation could not verify storage; retry later",
				},
			},
			503,
			noStore,
		);
	}
});

export { internal };
export default app;
