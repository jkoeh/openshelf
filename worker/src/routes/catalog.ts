import { createRoute, z } from "@hono/zod-openapi";
import {
	CACHE_SHORT,
	CATALOG_PAGE_SIZE_DEFAULT,
	CATALOG_PAGE_SIZE_MAX,
	R2_DEFAULT_RENDITION,
	R2_PREFIX_BOOKS,
} from "../constants";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

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

interface BookManifestRendition {
	current_build?: string;
}

interface BookManifestFile {
	title?: string;
	author?: string;
	source?: string;
	renditions?: Record<string, BookManifestRendition>;
}

interface RenditionManifestFile {
	total_duration_seconds?: number;
	chapters?: unknown[];
}

function parseBookManifestKey(key: string): { authorSlug: string; titleSlug: string } | null {
	const parts = key.split("/");
	if (parts.length !== 4 || parts[0] !== R2_PREFIX_BOOKS || parts[3] !== "manifest.json") {
		return null;
	}
	return { authorSlug: parts[1], titleSlug: parts[2] };
}

function chooseCatalogRendition(renditions: Record<string, BookManifestRendition>): string | null {
	const keys = Object.keys(renditions);
	if (keys.length === 0) {
		return null;
	}
	if (R2_DEFAULT_RENDITION in renditions) {
		return R2_DEFAULT_RENDITION;
	}
	return keys.sort()[0];
}

async function listBookManifestKeys(bucket: R2Bucket): Promise<string[]> {
	const keys: string[] = [];
	let cursor: string | undefined;

	do {
		const listed = await bucket.list({ prefix: `${R2_PREFIX_BOOKS}/`, cursor });
		for (const obj of listed.objects) {
			if (parseBookManifestKey(obj.key)) {
				keys.push(obj.key);
			}
		}
		cursor = listed.truncated ? listed.cursor : undefined;
	} while (cursor);

	return keys.sort();
}

async function keyExists(bucket: R2Bucket, key: string): Promise<boolean> {
	return (await bucket.head(key)) !== null;
}

async function deriveCatalog(bucket: R2Bucket): Promise<Required<CatalogFile>> {
	const books: CatalogBook[] = [];

	for (const key of await listBookManifestKeys(bucket)) {
		const meta = parseBookManifestKey(key);
		if (!meta) {
			continue;
		}

		const manifestObj = await bucket.get(key);
		if (!manifestObj) {
			continue;
		}
		const manifest = (await manifestObj.json()) as BookManifestFile;
		const renditions = manifest.renditions ?? {};
		const rendition = chooseCatalogRendition(renditions);
		if (!rendition) {
			continue;
		}

		const currentBuild = renditions[rendition]?.current_build;
		if (!currentBuild) {
			continue;
		}

		const renditionManifestObj = await bucket.get(
			r2Key.renditionManifest(meta.authorSlug, meta.titleSlug, rendition, currentBuild),
		);
		if (!renditionManifestObj) {
			continue;
		}
		const renditionManifest = (await renditionManifestObj.json()) as RenditionManifestFile;
		const chapters = renditionManifest.chapters ?? [];
		const hasCover =
			(await keyExists(bucket, r2Key.cover(meta.authorSlug, meta.titleSlug, "jpg"))) ||
			(await keyExists(bucket, r2Key.cover(meta.authorSlug, meta.titleSlug, "png")));

		books.push({
			author: manifest.author ?? meta.authorSlug,
			author_slug: meta.authorSlug,
			title: manifest.title ?? meta.titleSlug,
			title_slug: meta.titleSlug,
			source: manifest.source ?? "unknown",
			rendition,
			total_duration_seconds: renditionManifest.total_duration_seconds ?? 0,
			chapter_count: chapters.length,
			has_cover: hasCover,
		});
	}

	return {
		version: 1,
		generated_at: new Date().toISOString(),
		books,
	};
}

async function readCatalog(bucket: R2Bucket): Promise<Required<CatalogFile>> {
	const obj = await bucket.get("catalog.json");
	if (!obj) {
		return deriveCatalog(bucket);
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
