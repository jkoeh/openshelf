import { createRoute, z } from "@hono/zod-openapi";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { credentialStatus, noStore } from "../utils/job-auth";
import { JobErrorCode, publicJobError } from "../utils/generation-errors";
import { createOpenAPIApp } from "../utils/openapi-app";
import {
	SourceIdSchema,
	validSourceEpub,
	sourceEpubKey,
	gutenbergMirrorUrl,
} from "../utils/source-editions";
import {
	canonicalSearch,
	indexTokens,
	searchForms,
	searchTokens as tokens,
} from "../utils/source-search";

const BookSchema = z
	.object({
		source_id: z.string(),
		title: z.string(),
		author: z.string(),
		language: z.enum(["en", "zh"]),
		reading_cached: z.boolean(),
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
const SearchQuery = z.object({
	q: z
		.string()
		.min(2)
		.max(80)
		.describe(
			"Title or author in the current source index; Chinese matches Simplified and Traditional scripts",
		),
	limit: z.coerce.number().int().min(1).max(10).default(10),
	language: z.enum(["en", "zh"]).optional(),
});
const SyncBook = z.object({
	source_id: SourceIdSchema,
	title: z.string().min(1).max(300),
	author: z.string().min(1).max(200),
	epub_url: z.string().url().max(500),
	language: z.enum(["en", "zh"]).default("en"),
	provenance: z
		.record(z.unknown())
		.default({})
		.refine((value) => JSON.stringify(value).length <= 256 * 1024, "Provenance too large"),
});
const SyncBody = z.object({ books: z.array(SyncBook).min(1).max(50) });
const SyncResponse = z.object({ synced: z.number().int() });
const error = { description: "Error", content: { "application/json": { schema: ErrorSchema } } };
const searchRoute = createRoute({
	method: "get",
	path: "/",
	tags: ["sources"],
	summary: "Search the current D1 source index without upstream requests or result caching",
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
		params: z.object({ source_id: SourceIdSchema }),
		query: z.object({ inline: z.enum(["1"]).optional() }),
	},
	responses: {
		200: {
			description: "Inline source EPUB",
			content: { "application/epub+zip": { schema: { type: "string", format: "binary" } } },
		},
		502: error,
		302: {
			description: "Original source EPUB download",
			headers: { Location: { schema: { type: "string", format: "uri" } } },
		},
		400: error,
		404: error,
		429: error,
		503: error,
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();
app.use("*", async (c, next) => {
	c.header("Cache-Control", "no-store");
	await next();
});
const internal = createOpenAPIApp<{ Bindings: Env }>();
const uploadRoute = createRoute({
	method: "post",
	path: "/{source_id}/epub",
	tags: ["sources"],
	summary: "PC upload of a validated source reading copy (maximum 32 MiB)",
	request: {
		params: z.object({ source_id: SourceIdSchema }),
		body: { content: { "application/epub+zip": { schema: { type: "string", format: "binary" } } } },
	},
	responses: {
		200: {
			description: "Cached",
			content: { "application/json": { schema: z.object({ cached: z.literal(true) }) } },
		},
		400: error,
		401: error,
		404: error,
		409: error,
		413: error,
		429: error,
		503: error,
	},
});
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
	language: "en" | "zh";
	epub_sha256: string | null;
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
	const { q, limit, language } = c.req.valid("query");
	const query = canonicalSearch(q);
	const term = tokens(query).sort((a, b) => b.length - a.length)[0] ?? "";
	const chineseQuery = /\p{Script=Han}/u.test(q);
	if (term.length < 2) return c.json({ books: [] }, 200, noStore);
	const select = `SELECT DISTINCT b.source_id,b.title,b.author,b.language,b.epub_sha256,b.author_slug,b.title_slug,
		j.state,j.mode,j.id job_id,j.updated_at job_updated_at,j.error_code
		FROM source_tokens t JOIN source_books b ON b.source_id=t.source_id
		LEFT JOIN generation_jobs j ON j.id=COALESCE(
			(SELECT id FROM generation_jobs WHERE source_id=b.source_id
				AND state IN ('queued','running') LIMIT 1),
			(SELECT id FROM generation_jobs WHERE source_id=b.source_id AND stage<>'deleted'
				ORDER BY created_at DESC LIMIT 1))`;
	const languageClause = language ? " AND b.language=?" : "";
	const languageArgs = language ? [language] : [];
	const candidates = async (prefix: string) =>
		c.env
			.JOB_DB!.prepare(
				`${select} WHERE t.token >= ? AND t.token < ? AND b.source_id NOT LIKE 'ownerupload:%'${languageClause} LIMIT 80`,
			)
			.bind(prefix, `${prefix}\uffff`, ...languageArgs)
			.all<Row>();
	let rows: { results: Row[] };
	if (chineseQuery) {
		// Existing rows may contain only original-script tokens. Query their script
		// equivalents as well, without a full-table scan or a mandatory reimport.
		const fullPrefixes = [
			...new Set(
				searchForms(q)
					.map((form) => {
						const longest = tokens(form).sort((a, b) => b.length - a.length)[0] ?? "";
						return longest;
					})
					.filter((prefix) => Array.from(prefix).length >= 2),
			),
		];
		let matches = await Promise.all(fullPrefixes.map(candidates));
		if (!matches.some((match) => match.results.length)) {
			const bigrams = [
				...new Set(fullPrefixes.map((prefix) => Array.from(prefix).slice(0, 2).join(""))),
			];
			matches = await Promise.all(bigrams.map(candidates));
		}
		rows = {
			results: [
				...new Map(
					matches.flatMap((match) => match.results).map((row) => [row.source_id, row]),
				).values(),
			],
		};
	} else rows = await candidates(term);
	if (!chineseQuery && !rows.results.length && term.length >= 3 && term.length <= 12) {
		const variants = [
			...new Set(
				Array.from(
					{ length: term.length - 1 },
					(_, i) => term.slice(0, i) + term[i + 1] + term[i] + term.slice(i + 2),
				),
			),
		];
		rows = await c.env.JOB_DB.prepare(
			`${select} WHERE t.token IN (${variants.map(() => "?").join(",")}) AND b.source_id NOT LIKE 'ownerupload:%'${languageClause} LIMIT 80`,
		)
			.bind(...variants, ...languageArgs)
			.all<Row>();
	}
	if (!chineseQuery && !rows.results.length && term.length >= 3)
		rows = await candidates(term.slice(0, 2));
	const scored = rows.results
		.map((row) => {
			const title = canonicalSearch(row.title),
				author = canonicalSearch(row.author);
			const words = [...tokens(title), ...tokens(author)];
			const best = Math.min(...words.map((w) => distance(term, w)), 99);
			const score =
				title === query
					? 0
					: title.startsWith(query)
						? 1
						: author.startsWith(query)
							? 2
							: (chineseQuery && (title.includes(query) || author.includes(query))) ||
									(!chineseQuery && words.some((w) => w.startsWith(term)))
								? 3
								: !chineseQuery && best <= 2
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
			language: row.language,
			reading_cached: !!row.epub_sha256,
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
	return c.json(result, 200, noStore);
});

app.openapi(downloadRoute, async (c) => {
	if (!c.env.JOB_DB || !c.env.SEARCH_RATE_LIMITER)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "EPUB downloads are not configured" } },
			503,
			noStore,
		);
	const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
	if (!(await c.env.SEARCH_RATE_LIMITER.limit({ key: `source-epub:${ip}` })).success)
		return c.json({ error: { code: "RATE_LIMITED", message: "Try again later" } }, 429, noStore);
	const { source_id } = c.req.valid("param");
	const source = await c.env.JOB_DB.prepare("SELECT epub_url FROM source_books WHERE source_id=?")
		.bind(source_id)
		.first<{ epub_url: string }>();
	if (!source)
		return c.json(
			{ error: { code: "NOT_FOUND", message: "Source edition not indexed" } },
			404,
			noStore,
		);
	const key = await sourceEpubKey(source_id);
	const cachedHead = c.req.method === "HEAD" ? await c.env.R2_BUCKET.head(key) : null;
	const cachedBody = c.req.method === "HEAD" ? null : await c.env.R2_BUCKET.get(key);
	const cached = cachedHead ?? cachedBody;
	if (cached)
		return new Response(cachedBody?.body ?? null, {
			headers: {
				...noStore,
				"Content-Type": "application/epub+zip",
				"Content-Length": String(cached.size),
				ETag: cached.httpEtag,
				...(cached.customMetadata?.sha256
					? { "X-Content-SHA256": cached.customMetadata.sha256 }
					: {}),
			},
		});
	if (c.req.valid("query").inline === "1" || source_id.startsWith("wikisource:"))
		return c.json(
			{
				error: {
					code: "SOURCE_UNAVAILABLE",
					message: "This edition has no validated reading copy yet.",
				},
			},
			502,
			noStore,
		);
	let url: URL;
	try {
		url = new URL(source.epub_url);
	} catch {
		return c.json(
			{ error: { code: "INVALID_SOURCE", message: "Source EPUB URL is invalid" } },
			400,
			noStore,
		);
	}
	if (!validSourceEpub(url, source_id))
		return c.json(
			{ error: { code: "INVALID_SOURCE", message: "Source EPUB URL must match the edition ID" } },
			400,
			noStore,
		);
	if (source_id.startsWith("standardebooks:")) url.search = "?source=download";
	if (source_id.startsWith("gutenberg:")) url = gutenbergMirrorUrl(url, source_id)!;
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
		if (
			!validSourceEpub(url, book.source_id) ||
			(book.source_id.startsWith("wikisource:") && book.source_id.split(":")[1] !== book.language)
		)
			return c.json(
				{ error: { code: "INVALID_SOURCE", message: "EPUB URL must match the edition ID" } },
				400,
			);
	}
	const now = new Date().toISOString();
	const statements = books.flatMap((book) => {
		const words = indexTokens(book.title, book.author);
		if (!words.length) words.push(book.source_id.slice(10));
		return [
			c.env
				.JOB_DB!.prepare(`INSERT INTO source_books(source_id,title,author,epub_url,updated_at,language,provenance) VALUES(?,?,?,?,?,?,?)
			ON CONFLICT(source_id) DO UPDATE SET title=excluded.title,author=excluded.author,epub_url=excluded.epub_url,updated_at=excluded.updated_at,
			language=CASE WHEN source_books.epub_sha256 IS NULL THEN excluded.language ELSE source_books.language END,
			provenance=CASE WHEN source_books.epub_sha256 IS NULL THEN excluded.provenance ELSE source_books.provenance END`)
				.bind(
					book.source_id,
					book.title,
					book.author,
					book.source_id.startsWith("gutenberg:")
						? gutenbergMirrorUrl(new URL(book.epub_url), book.source_id)!.href
						: book.epub_url,
					now,
					book.language,
					JSON.stringify(book.provenance),
				),
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

internal.openapi(uploadRoute, async (c) => {
	if (!c.env.JOB_DB || !c.env.PC_TOKEN || !c.env.AUTH_RATE_LIMITER)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Source cache is not configured" } },
			503,
			noStore,
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
			noStore,
		);
	const { source_id } = c.req.valid("param");
	if (
		!(await c.env.JOB_DB.prepare("SELECT 1 FROM source_books WHERE source_id=?")
			.bind(source_id)
			.first())
	)
		return c.json(
			{ error: { code: "NOT_FOUND", message: "Source edition not indexed" } },
			404,
			noStore,
		);
	const length = Number(c.req.header("Content-Length"));
	if (!Number.isInteger(length) || length < 4 || length > 32 * 1024 * 1024)
		return c.json(
			{ error: { code: "INVALID_SIZE", message: "EPUB must be between 4 bytes and 32 MiB" } },
			413,
			noStore,
		);
	if (c.req.header("Content-Type") !== "application/epub+zip" || !c.req.raw.body)
		return c.json(
			{ error: { code: "INVALID_EPUB", message: "An EPUB body is required" } },
			400,
			noStore,
		);
	const reader = c.req.raw.body.getReader();
	const bytes = new Uint8Array(length);
	let size = 0;
	try {
		while (true) {
			const { value, done } = await reader.read();
			if (done) break;
			if (size + value.length > length) {
				await reader.cancel();
				return c.json(
					{ error: { code: "INVALID_SIZE", message: "EPUB exceeds declared length" } },
					413,
					noStore,
				);
			}
			bytes.set(value, size);
			size += value.length;
		}
	} finally {
		reader.releaseLock();
	}
	if (size !== length || bytes[0] !== 80 || bytes[1] !== 75 || bytes[2] !== 3 || bytes[3] !== 4)
		return c.json(
			{ error: { code: "INVALID_EPUB", message: "EPUB ZIP data is required" } },
			400,
			noStore,
		);
	const digest = await crypto.subtle.digest("SHA-256", bytes);
	const sha256 = Array.from(new Uint8Array(digest), (byte) =>
		byte.toString(16).padStart(2, "0"),
	).join("");
	const key = await sourceEpubKey(source_id);
	const stored = await c.env.R2_BUCKET.put(key, bytes, {
		httpMetadata: { contentType: "application/epub+zip" },
		customMetadata: { sha256, source_id },
		onlyIf: { etagDoesNotMatch: "*" },
	});
	if (!stored) {
		const existing = await c.env.R2_BUCKET.head(key);
		if (existing?.customMetadata?.sha256 !== sha256)
			return c.json(
				{
					error: {
						code: "EDITION_CONFLICT",
						message: "A different reading copy already exists for this edition",
					},
				},
				409,
				noStore,
			);
	}
	await c.env.JOB_DB.prepare("UPDATE source_books SET epub_sha256=? WHERE source_id=?")
		.bind(sha256, source_id)
		.run();
	return c.json({ cached: true as const }, 200, noStore);
});
