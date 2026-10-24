import { z } from "zod";
import { R2_DEFAULT_RENDITION } from "../constants";
import type { Env } from "../types";
import { r2Key } from "./r2-keys";
import { canonicalSearch } from "./source-search";

const buildId = z.string().regex(/^[a-f0-9]{16}$/);
const bookSchema = z.object({
	title: z.string().min(1).max(300),
	author: z.string().min(1).max(200),
	source: z.string().min(1).max(80),
	renditions: z.record(z.object({ current_build: buildId })),
});
const renditionSchema = z.object({
	version: z.literal(2),
	build: buildId,
	total_duration_seconds: z.number().finite().nonnegative(),
	sections: z
		.array(z.object({ sequence: z.number().int().positive() }))
		.min(1)
		.max(5000),
});
type WatchedObject = { key: string; etag: string | null };
class InvalidPublication extends Error {
	constructor(
		message: string,
		readonly watched: WatchedObject[],
	) {
		super(message);
	}
}

const activePublication = `EXISTS(SELECT 1 FROM book_deletions WHERE author_slug=? AND title_slug=?) OR
	EXISTS(SELECT 1 FROM generation_jobs j JOIN source_books s ON s.source_id=j.source_id
	WHERE s.author_slug=? AND s.title_slug=? AND j.state IN ('queued','running'))`;

async function isActive(env: Env, author: string, title: string): Promise<boolean> {
	return !!(await env
		.JOB_DB!.prepare(`SELECT 1 WHERE ${activePublication}`)
		.bind(author, title, author, title)
		.first());
}

async function readPublication(env: Env, author: string, title: string) {
	const watched: WatchedObject[] = [];
	const read = async (key: string) => {
		const object = await env.R2_BUCKET.get(key);
		watched.push({ key, etag: object?.etag ?? null });
		if (!object) throw new InvalidPublication("Publication manifest is missing", watched);
		try {
			return await object.json();
		} catch (error) {
			if (error instanceof SyntaxError)
				throw new InvalidPublication("Publication manifest is invalid JSON", watched);
			throw error;
		}
	};
	const parsedBook = bookSchema.safeParse(await read(r2Key.bookManifest(author, title)));
	if (!parsedBook.success)
		throw new InvalidPublication("Publication book metadata is incompatible", watched);
	const book = parsedBook.data;
	const keys = Object.keys(book.renditions).sort();
	const rendition = R2_DEFAULT_RENDITION in book.renditions ? R2_DEFAULT_RENDITION : keys[0];
	if (!rendition) throw new InvalidPublication("Publication has no rendition", watched);
	const build = book.renditions[rendition].current_build;
	const parsedManifest = renditionSchema.safeParse(
		await read(r2Key.renditionManifest(author, title, rendition, build)),
	);
	if (!parsedManifest.success)
		throw new InvalidPublication("Publication rendition is incompatible", watched);
	const manifest = parsedManifest.data;
	if (manifest.build !== build)
		throw new InvalidPublication("Publication build does not match its manifest", watched);
	if (
		new Set(manifest.sections.map((section) => section.sequence)).size !== manifest.sections.length
	)
		throw new InvalidPublication("Publication has duplicate section sequences", watched);
	const hasCover =
		!!(await env.R2_BUCKET.head(r2Key.cover(author, title, "jpg"))) ||
		!!(await env.R2_BUCKET.head(r2Key.cover(author, title, "png")));
	return { book, rendition, build, manifest, hasCover };
}

/** Derive public catalog metadata only from the current actual R2 publication. */
export async function syncPublishedBook(env: Env, author: string, title: string): Promise<boolean> {
	if (!env.JOB_DB) throw new Error("Catalog database is not configured");
	if (
		await env.JOB_DB.prepare("SELECT 1 FROM book_deletions WHERE author_slug=? AND title_slug=?")
			.bind(author, title)
			.first()
	)
		return false;
	let publication;
	try {
		publication = await readPublication(env, author, title);
	} catch (error) {
		if (
			error instanceof InvalidPublication &&
			error.watched.length === 1 &&
			error.watched[0].etag === null
		)
			return false;
		throw error;
	}
	const { book, rendition, build, manifest, hasCover } = publication;
	const result = await env.JOB_DB.prepare(`INSERT INTO published_books
		(author_slug,title_slug,author,title,source,rendition,current_build,total_duration_seconds,section_count,has_cover,title_search,author_search,updated_at,revision)
		SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM book_deletions WHERE author_slug=? AND title_slug=?)
		ON CONFLICT(author_slug,title_slug) DO UPDATE SET author=excluded.author,title=excluded.title,source=excluded.source,
		rendition=excluded.rendition,current_build=excluded.current_build,total_duration_seconds=excluded.total_duration_seconds,
		section_count=excluded.section_count,has_cover=excluded.has_cover,title_search=excluded.title_search,
		author_search=excluded.author_search,updated_at=excluded.updated_at,revision=excluded.revision`)
		.bind(
			author,
			title,
			book.author,
			book.title,
			book.source,
			rendition,
			build,
			manifest.total_duration_seconds,
			manifest.sections.length,
			hasCover ? 1 : 0,
			canonicalSearch(book.title),
			canonicalSearch(book.author),
			new Date().toISOString(),
			crypto.randomUUID(),
			author,
			title,
		)
		.run();
	return !!result.meta.changes;
}

export interface PublicationCursor {
	author_slug: string;
	title_slug: string;
}

/** Bounded reconciliation; known invalid objects are distinguished from storage failures. */
export async function reconcilePublishedBooks(env: Env, after?: PublicationCursor) {
	const where = after ? "WHERE author_slug>? OR (author_slug=? AND title_slug>?)" : "";
	const statement = env.JOB_DB!.prepare(
		`SELECT author_slug,title_slug,revision FROM published_books ${where} ORDER BY author_slug,title_slug LIMIT 10`,
	);
	const rows = await (after
		? statement.bind(after.author_slug, after.author_slug, after.title_slug)
		: statement
	).all<PublicationCursor & { revision: string }>();
	let removed = 0;
	for (const row of rows.results) {
		const { author_slug: author, title_slug: title } = row;
		if (await isActive(env, author, title)) continue;
		try {
			await readPublication(env, author, title);
			await syncPublishedBook(env, author, title);
			continue;
		} catch (error) {
			if (!(error instanceof InvalidPublication)) throw error;
			let unchanged = true;
			for (const watched of error.watched) {
				if ((await env.R2_BUCKET.head(watched.key))?.etag !== (watched.etag ?? undefined)) {
					unchanged = false;
					break;
				}
			}
			if (!unchanged) continue;
			const eligible = `EXISTS(SELECT 1 FROM published_books WHERE author_slug=? AND title_slug=? AND revision=? AND NOT (${activePublication}))`;
			const values = [author, title, row.revision, author, title, author, title];
			const database = env.JOB_DB!;
			// One D1 transaction preserves source/job consistency and makes the same
			// revision/active-work guard protect every destructive update.
			const results = await database.batch([
				database
					.prepare(`UPDATE generation_jobs SET state='canceled',stage='deleted',attempts=3,author_slug=NULL,title_slug=NULL,updated_at=?
					WHERE source_id IN(SELECT source_id FROM source_books WHERE author_slug=? AND title_slug=?) AND ${eligible}`)
					.bind(new Date().toISOString(), author, title, ...values),
				database
					.prepare(
						`UPDATE source_books SET author_slug=NULL,title_slug=NULL WHERE author_slug=? AND title_slug=? AND ${eligible}`,
					)
					.bind(author, title, ...values),
				database
					.prepare(
						`DELETE FROM published_books WHERE author_slug=? AND title_slug=? AND revision=? AND NOT (${activePublication})`,
					)
					.bind(...values),
			]);
			removed += results[2].meta.changes;
		}
	}
	const last = rows.results.at(-1);
	return {
		checked: rows.results.length,
		removed,
		after:
			rows.results.length === 10 && last
				? { author_slug: last.author_slug, title_slug: last.title_slug }
				: null,
	};
}
