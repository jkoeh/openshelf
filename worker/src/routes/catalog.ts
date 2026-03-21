import { Hono } from "hono";
import { CACHE_SHORT, CATALOG_PAGE_SIZE_DEFAULT, CATALOG_PAGE_SIZE_MAX } from "../constants";
import type { Env } from "../types";
import { r2Key } from "../utils/r2-keys";
import { notFound } from "../utils/response";

interface CatalogBook {
	author: string;
	author_slug: string;
	title: string;
	title_slug: string;
	source: string;
	rendition: string;
	total_duration_seconds: number;
	chapter_count: number;
}

interface Catalog {
	version: number;
	generated_at: string;
	books: CatalogBook[];
}

const app = new Hono<{ Bindings: Env }>();

app.get("/", async (c) => {
	const obj = await c.env.R2_BUCKET.get(r2Key.catalog());
	if (!obj) {
		return notFound("Catalog not found");
	}

	const catalog: Catalog = await obj.json();

	let books = catalog.books;

	// Filter by query (case-insensitive substring on title + author)
	const q = c.req.query("q")?.toLowerCase();
	if (q) {
		books = books.filter(
			(b) => b.title.toLowerCase().includes(q) || b.author.toLowerCase().includes(q),
		);
	}

	// Filter by author
	const authorFilter = c.req.query("author")?.toLowerCase();
	if (authorFilter) {
		books = books.filter((b) => b.author.toLowerCase().includes(authorFilter));
	}

	// Paginate
	const page = Math.max(1, Number(c.req.query("page")) || 1);
	const limit = Math.min(
		CATALOG_PAGE_SIZE_MAX,
		Math.max(1, Number(c.req.query("limit")) || CATALOG_PAGE_SIZE_DEFAULT),
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
