import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_SHORT, CATALOG_PAGE_SIZE_DEFAULT, CATALOG_PAGE_SIZE_MAX } from "../constants";
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
		version: z.number().int().positive(),
		generated_at: z.string(),
		books: z.array(CatalogBookSchema),
		total: z.number().int().nonnegative(),
		page: z.number().int().positive(),
		limit: z.number().int().positive(),
	})
	.openapi("Catalog");

const QuerySchema = z.object({
	q: z
		.string()
		.optional()
		.openapi({ description: "Case-insensitive substring filter on title + author" }),
	author: z
		.string()
		.optional()
		.openapi({ description: "Case-insensitive substring filter on author" }),
	page: z.string().optional().openapi({ example: "1" }),
	limit: z.string().optional().openapi({ example: "20" }),
});

interface CatalogBook extends z.infer<typeof CatalogBookSchema> {}

interface CatalogFile {
	version?: number;
	generated_at?: string;
	books?: CatalogBook[];
}

async function readCatalog(bucket: R2Bucket): Promise<Required<CatalogFile>> {
	const obj = await bucket.get("catalog.json");
	if (!obj) {
		return { version: 1, generated_at: "", books: [] };
	}
	const catalog = (await obj.json()) as CatalogFile;
	return {
		version: catalog.version ?? 1,
		generated_at: catalog.generated_at ?? "",
		books: catalog.books ?? [],
	};
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
	const catalog = await readCatalog(c.env.R2_BUCKET);
	let books = catalog.books;

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
			version: catalog.version,
			generated_at: catalog.generated_at,
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
