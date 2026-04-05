import { Hono } from "hono";
import {
	CACHE_SHORT,
	CATALOG_PAGE_SIZE_DEFAULT,
	CATALOG_PAGE_SIZE_MAX,
	R2_PREFIX_BOOKS,
} from "../constants";
import type { Env } from "../types";

interface CatalogBook {
	author: string;
	author_slug: string;
	title: string;
	title_slug: string;
	source: string;
	rendition: string;
	total_duration_seconds: number;
	chapter_count: number;
	has_cover: boolean;
}

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

	// List all objects under books/ and filter for manifest.json
	do {
		const listed = await bucket.list({ prefix: `${R2_PREFIX_BOOKS}/`, cursor });

		const manifestObjects = listed.objects.filter((obj) => obj.key.endsWith("/manifest.json"));

		// Fetch all manifests in parallel
		const results = await Promise.all(
			manifestObjects.map(async (obj) => {
				const parts = obj.key.split("/");
				// books/<author>/<title>/audio/<rendition>/manifest.json
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

const app = new Hono<{ Bindings: Env }>();

app.get("/", async (c) => {
	let books = await buildCatalog(c.env.R2_BUCKET);

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
