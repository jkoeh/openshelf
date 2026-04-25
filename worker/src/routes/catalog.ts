import { createRoute, z } from "@hono/zod-openapi";
import {
	CACHE_SHORT,
	CATALOG_PAGE_SIZE_DEFAULT,
	CATALOG_PAGE_SIZE_MAX,
	R2_PREFIX_BOOKS,
} from "../constants";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";

const CatalogBookSchema = z
	.object({
		author: z.string(),
		author_slug: z.string(),
		title: z.string(),
		title_slug: z.string(),
		source: z.string(),
		rendition: z.string(),
		total_duration_seconds: z.number(),
		chapter_count: z.number().int(),
		has_cover: z.boolean(),
	})
	.openapi("CatalogBook");

const CatalogResponseSchema = z
	.object({
		books: z.array(CatalogBookSchema),
		total: z.number().int().nonnegative(),
		page: z.number().int().positive(),
		limit: z.number().int().positive(),
	})
	.openapi("Catalog");

const QuerySchema = z.object({
	q: z.string().optional().openapi({ description: "Case-insensitive substring filter on title + author" }),
	author: z.string().optional().openapi({ description: "Case-insensitive substring filter on author" }),
	page: z.string().optional().openapi({ example: "1" }),
	limit: z.string().optional().openapi({ example: "20" }),
});

interface CatalogBook extends z.infer<typeof CatalogBookSchema> {}

interface Manifest {
	title: string;
	author: string;
	source: string;
	rendition: string;
	total_duration_seconds: number;
	chapters: unknown[];
}

/**
 * Dynamically build the catalog by listing all manifest.json files on R2.
 * Key pattern: books/<author>/<title>/audio/<rendition>/manifest.json
 */
async function buildCatalog(bucket: R2Bucket): Promise<CatalogBook[]> {
	const books: CatalogBook[] = [];
	let cursor: string | undefined;

	do {
		const listed = await bucket.list({ prefix: `${R2_PREFIX_BOOKS}/`, cursor });

		const manifestObjects = listed.objects.filter((obj) => obj.key.endsWith("/manifest.json"));

		const results = await Promise.all(
			manifestObjects.map(async (obj) => {
				const parts = obj.key.split("/");
				if (parts.length !== 6) return null;

				const [r2Obj, coverJpg, coverPng] = await Promise.all([
					bucket.get(obj.key),
					bucket.head(`${R2_PREFIX_BOOKS}/${parts[1]}/${parts[2]}/cover.jpg`),
					bucket.head(`${R2_PREFIX_BOOKS}/${parts[1]}/${parts[2]}/cover.png`),
				]);
				if (!r2Obj) return null;

				const manifest: Manifest = await r2Obj.json();
				return {
					author: manifest.author || parts[1],
					author_slug: parts[1],
					title: manifest.title || parts[2],
					title_slug: parts[2],
					source: manifest.source || "unknown",
					rendition: parts[4],
					total_duration_seconds: manifest.total_duration_seconds || 0,
					chapter_count: manifest.chapters?.length || 0,
					has_cover: !!(coverJpg || coverPng),
				} satisfies CatalogBook;
			}),
		);

		for (const book of results) {
			if (book) books.push(book);
		}

		cursor = listed.truncated ? listed.cursor : undefined;
	} while (cursor);

	return books;
}

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["catalog"],
	summary: "List all available books",
	request: { query: QuerySchema },
	responses: {
		200: {
			description: "Filtered + paginated catalog",
			content: { "application/json": { schema: CatalogResponseSchema } },
		},
		400: {
			description: "Invalid query params",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { q, author: authorFilter, page: pageStr, limit: limitStr } = c.req.valid("query");
	let books = await buildCatalog(c.env.R2_BUCKET);

	if (q) {
		const ql = q.toLowerCase();
		books = books.filter(
			(b) => b.title.toLowerCase().includes(ql) || b.author.toLowerCase().includes(ql),
		);
	}

	if (authorFilter) {
		const al = authorFilter.toLowerCase();
		books = books.filter((b) => b.author.toLowerCase().includes(al));
	}

	const page = Math.max(1, Number(pageStr) || 1);
	const limit = Math.min(
		CATALOG_PAGE_SIZE_MAX,
		Math.max(1, Number(limitStr) || CATALOG_PAGE_SIZE_DEFAULT),
	);
	const start = (page - 1) * limit;
	const paged = books.slice(start, start + limit);

	return c.json(
		{
			books: paged,
			total: books.length,
			page,
			limit,
		},
		200,
		{ "Cache-Control": CACHE_SHORT },
	);
});

export default app;
