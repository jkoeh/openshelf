import { createRoute, z } from "@hono/zod-openapi";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { credentialStatus, noStore } from "../utils/job-auth";
import { JobErrorCode, publicJobError } from "../utils/generation-errors";
import { createOpenAPIApp } from "../utils/openapi-app";

const BookSchema = z
	.object({
		source_id: z.string(),
		title: z.string(),
		author: z.string(),
		state: z.enum(["ready_to_generate", "queued", "running", "failed", "available"]),
		job_state: z.enum(["queued", "running", "completed", "failed", "canceled"]).nullable(),
		job_mode: z.enum(["standard", "expressive"]).nullable(),
		job_id: z.string().nullable(),
		job_updated_at: z.string().nullable(),
		job_error_code: JobErrorCode.nullable(),
		author_slug: z.string().nullable(),
		title_slug: z.string().nullable(),
	})
	.openapi("SourceBook");
const SearchResponse = z.object({ books: z.array(BookSchema) }).openapi("SourceSearch");
const suggestionCache = new Map<string, { expires: number; result: z.infer<typeof SearchResponse> }>();
const SearchQuery = z.object({
	q: z.string().min(2).max(80),
	limit: z.coerce.number().int().min(1).max(10).default(10),
});
const SyncBook = z.object({
	source_id: z.string().regex(/^gutenberg:[1-9][0-9]*$/),
	title: z.string().min(1).max(300),
	author: z.string().min(1).max(200),
	epub_url: z.string().url().max(500),
});
const SyncBody = z.object({ books: z.array(SyncBook).min(1).max(50) });
const SyncResponse = z.object({ synced: z.number().int() });
const error = { description: "Error", content: { "application/json": { schema: ErrorSchema } } };
const searchRoute = createRoute({
	method: "get",
	path: "/",
	tags: ["sources"],
	summary: "Find Gutenberg editions by title or author",
	request: { query: SearchQuery },
	responses: {
		200: {
			description: "Suggestions",
			content: { "application/json": { schema: SearchResponse } },
		},
		400: error,
		429: error,
		503: error,
	},
});
const syncRoute = createRoute({
	method: "post",
	path: "/sync",
	tags: ["sources"],
	summary: "PC source index batch",
	request: { body: { content: { "application/json": { schema: SyncBody } } } },
	responses: {
		200: { description: "Upserted", content: { "application/json": { schema: SyncResponse } } },
		400: error,
		401: error,
		429: error,
		503: error,
	},
});
const downloadRoute = createRoute({
	method: "get",
	path: "/{source_id}/epub",
	tags: ["sources"],
	summary: "Download the source EPUB independently of audio generation",
	request: {
		params: z.object({ source_id: z.string().regex(/^gutenberg:[1-9][0-9]*$/) }),
		query: z.object({ inline: z.enum(["1"]).optional() }),
	},
	responses: {
		200: { description: "Inline source EPUB", content: { "application/epub+zip": { schema: { type: "string", format: "binary" } } } },
		502: error,
		302: {
			description: "Original Gutenberg EPUB download",
			headers: { Location: { schema: { type: "string", format: "uri" } } },
		},
		400: error,
		404: error,
		429: error,
		503: error,
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();
const internal = createOpenAPIApp<{ Bindings: Env }>();
const clean = (s: string) =>
	s
		.toLowerCase()
		.normalize("NFKD")
		.replace(/[\u0300-\u036f]/g, "")
		.replace(/[^a-z0-9]+/g, " ")
		.trim();
const tokens = (s: string) => [...new Set(clean(s).split(" ").filter(Boolean))];
function distance(a: string, b: string): number {
	let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
	for (let i = 1; i <= a.length; i++) {
		const next = [i];
		for (let j = 1; j <= b.length; j++)
			next[j] = Math.min(next[j - 1] + 1, prev[j] + 1, prev[j - 1] + Number(a[i - 1] !== b[j - 1]));
		prev = next;
	}
	return prev[b.length];
}

interface Row {
	source_id: string;
	title: string;
	author: string;
	author_slug: string | null;
	title_slug: string | null;
	state: string | null;
	mode: string | null;
	job_id: string | null;
	job_updated_at: string | null;
	error_code: string | null;
}
app.openapi(searchRoute, async (c) => {
	if (!c.env.JOB_DB || !c.env.SEARCH_RATE_LIMITER)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Source search is not configured" } },
			503,
		);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (!(await c.env.SEARCH_RATE_LIMITER.limit({ key: `search:${ip}` })).success)
		return c.json({ error: { code: "RATE_LIMITED", message: "Try again later" } }, 429);
	const { q, limit } = c.req.valid("query");
	const term = tokens(q).sort((a, b) => b.length - a.length)[0] ?? "";
	if (term.length < 2) return c.json({ books: [] }, 200, noStore);
	const cacheKey = JSON.stringify([new URL(c.req.url).origin, clean(q), limit]);
	const cached = suggestionCache.get(cacheKey);
	if (cached && cached.expires > Date.now()) return c.json(cached.result, 200, noStore);
	if (cached) suggestionCache.delete(cacheKey);
	const select = `SELECT DISTINCT b.source_id,b.title,b.author,b.author_slug,b.title_slug,
		j.state,j.mode,j.id job_id,j.updated_at job_updated_at,j.error_code
		FROM source_tokens t JOIN source_books b ON b.source_id=t.source_id
		LEFT JOIN generation_jobs j ON j.id=COALESCE(
			(SELECT id FROM generation_jobs WHERE source_id=b.source_id
				AND state IN ('queued','running') LIMIT 1),
			(SELECT id FROM generation_jobs WHERE source_id=b.source_id
				ORDER BY created_at DESC LIMIT 1))`;
	const candidates = async (prefix: string) =>
		c.env.JOB_DB!.prepare(`${select} WHERE t.token >= ? AND t.token < ? LIMIT 80`)
			.bind(prefix, `${prefix}\uffff`)
			.all<Row>();
	let rows = await candidates(term.length >= 3 ? term : term.slice(0, 2));
	if (!rows.results.length && term.length >= 3 && term.length <= 12) {
		const variants = [...new Set(Array.from({ length: term.length - 1 }, (_, i) =>
			term.slice(0, i) + term[i + 1] + term[i] + term.slice(i + 2),
		))];
		rows = await c.env.JOB_DB.prepare(
			`${select} WHERE t.token IN (${variants.map(() => "?").join(",")}) LIMIT 80`,
		).bind(...variants).all<Row>();
	}
	if (!rows.results.length && term.length >= 3) rows = await candidates(term.slice(0, 2));
	const scored = rows.results
		.map((row) => {
			const title = clean(row.title),
				author = clean(row.author);
			const words = [...tokens(row.title), ...tokens(row.author)];
			const best = Math.min(...words.map((w) => distance(term, w)), 99);
			const score =
				title === clean(q)
					? 0
					: title.startsWith(clean(q))
						? 1
						: author.startsWith(clean(q))
							? 2
							: words.some((w) => w.startsWith(term))
								? 3
								: best <= 2
									? 4 + best
									: 99;
			return { row, score };
		})
		.filter((x) => x.score < 99)
		.sort((a, b) => a.score - b.score || a.row.title.localeCompare(b.row.title))
		.slice(0, limit);
	const result = {
		books: scored.map(({ row }) => ({
				source_id: row.source_id,
				title: row.title,
				author: row.author,
				state: (row.author_slug && row.title_slug
					? "available"
					: row.state === "queued" || row.state === "running" || row.state === "failed"
						? row.state
						: "ready_to_generate") as z.infer<typeof BookSchema>["state"],
				job_id: row.job_id,
				job_updated_at: row.job_updated_at,
				job_error_code: publicJobError(row.error_code),
				job_state: row.state as z.infer<typeof BookSchema>["job_state"],
				job_mode: row.mode as z.infer<typeof BookSchema>["job_mode"],
				author_slug: row.author_slug,
				title_slug: row.title_slug,
			})),
	};
	if (suggestionCache.size >= 128) suggestionCache.delete(suggestionCache.keys().next().value!);
	suggestionCache.set(cacheKey, { expires: Date.now() + 15_000, result });
	return c.json(result, 200, noStore);
});

app.openapi(downloadRoute, async (c) => {
	if (!c.env.JOB_DB || !c.env.SEARCH_RATE_LIMITER)
		return c.json({ error: { code: "UNAVAILABLE", message: "EPUB downloads are not configured" } }, 503, noStore);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (!(await c.env.SEARCH_RATE_LIMITER.limit({ key: `source-epub:${ip}` })).success)
		return c.json({ error: { code: "RATE_LIMITED", message: "Try again later" } }, 429, noStore);
	const { source_id } = c.req.valid("param");
	const source = await c.env.JOB_DB.prepare("SELECT epub_url FROM source_books WHERE source_id=?")
		.bind(source_id).first<{ epub_url: string }>();
	if (!source)
		return c.json({ error: { code: "NOT_FOUND", message: "Source edition not indexed" } }, 404, noStore);
	let url: URL;
	try {
		url = new URL(source.epub_url);
	} catch {
		return c.json({ error: { code: "INVALID_SOURCE", message: "Source EPUB URL is invalid" } }, 400, noStore);
	}
	const number = source_id.slice(10);
	const path = new RegExp(`^/(?:ebooks/${number}\\.epub(?:3)?(?:\\.(?:images|noimages))?|cache/epub/${number}/pg${number}(?:-images)?(?:-3)?\\.epub)$`);
	if (url.protocol !== "https:" || url.hostname !== "www.gutenberg.org" ||
		url.username || url.password || url.port || url.search || url.hash || !path.test(url.pathname))
		return c.json({ error: { code: "INVALID_SOURCE", message: "Source EPUB URL must match the Gutenberg ID" } }, 400, noStore);
	if (c.req.valid("query").inline === "1") {
		try {
			let upstream: Response | undefined;
			for (let redirects = 0; redirects <= 3; redirects++) {
				upstream = await fetch(url.href, { redirect: "manual", signal: AbortSignal.timeout(30_000) });
				if (![301, 302, 303, 307, 308].includes(upstream.status)) break;
				const location = upstream.headers.get("Location");
				await upstream.body?.cancel();
				if (!location) throw new Error("Invalid redirect");
				url = new URL(location, url);
				if (url.protocol !== "https:" || url.hostname !== "www.gutenberg.org" || url.username || url.password || url.port || url.search || url.hash || !path.test(url.pathname)) throw new Error("Invalid redirect");
			}
			if (!upstream?.ok || !upstream.body) throw new Error("Source unavailable");
			return new Response(upstream.body, { headers: { ...noStore, "Content-Type": "application/epub+zip" } });
		} catch {
			return c.json({ error: { code: "SOURCE_UNAVAILABLE", message: "Could not load this EPUB. Try again shortly." } }, 502, noStore);
		}
	}
	return new Response(null, { status: 302, headers: { ...noStore, Location: url.href } });
});

internal.openapi(syncRoute, async (c) => {
	if (!c.env.JOB_DB || !c.env.PC_TOKEN || !c.env.AUTH_RATE_LIMITER)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Source sync is not configured" } },
			503,
		);
	const auth = await credentialStatus(c.req.raw, c.env, c.env.PC_TOKEN);
	if (auth)
		return c.json(
			{
				error: {
					code: auth === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			auth,
		);
	const { books } = c.req.valid("json");
	for (const book of books) {
		const url = new URL(book.epub_url);
		const sourceNumber = book.source_id.slice(10);
		const path = new RegExp(`^/(?:ebooks|cache/epub)/${sourceNumber}(?:[./-]|$)`);
		if (
			url.protocol !== "https:" ||
			url.hostname !== "www.gutenberg.org" ||
			url.username ||
			url.password ||
			url.port ||
			!path.test(url.pathname)
		)
			return c.json(
				{ error: { code: "INVALID_SOURCE", message: "EPUB URL must match the Gutenberg ID" } },
				400,
			);
	}
	const now = new Date().toISOString();
	const statements = books.flatMap((book) => {
		const words = [...new Set([...tokens(book.title), ...tokens(book.author)])].slice(0, 32);
		if (!words.length) words.push(book.source_id.slice(10));
		return [
			c.env
				.JOB_DB!.prepare(`INSERT INTO source_books(source_id,title,author,epub_url,updated_at) VALUES(?,?,?,?,?)
			ON CONFLICT(source_id) DO UPDATE SET title=excluded.title,author=excluded.author,epub_url=excluded.epub_url,updated_at=excluded.updated_at`)
				.bind(book.source_id, book.title, book.author, book.epub_url, now),
			c.env.JOB_DB!.prepare("DELETE FROM source_tokens WHERE source_id=?").bind(book.source_id),
			c.env
				.JOB_DB!.prepare(
					`INSERT INTO source_tokens(token,source_id) VALUES ${words.map(() => "(?,?)").join(",")}`,
				)
				.bind(...words.flatMap((word) => [word, book.source_id])),
		];
	});
	await c.env.JOB_DB.batch(statements);
	return c.json({ synced: books.length }, 200, noStore);
});
export { internal };
export default app;
