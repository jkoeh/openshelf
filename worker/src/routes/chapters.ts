import { Hono } from "hono";
import { CACHE_IMMUTABLE } from "../constants";
import type { Env } from "../types";
import { r2Key } from "../utils/r2-keys";
import { badRequest, notFound } from "../utils/response";
import { isValidSlug } from "../utils/validation";

interface ChunksChapter {
	number: number;
	title: string;
	chunks: string[];
}

interface ChunksData {
	version: number;
	chapters: ChunksChapter[];
}

const app = new Hono<{ Bindings: Env }>();

app.get("/", async (c) => {
	const author = c.req.param("author");
	const title = c.req.param("title");
	const numberParam = c.req.param("number") ?? "";

	if (!isValidSlug(author) || !isValidSlug(title)) {
		return badRequest("Invalid author or title slug");
	}

	const chapterNum = Number(numberParam);
	if (!Number.isInteger(chapterNum) || chapterNum < 1) {
		return badRequest("Invalid chapter number");
	}

	const obj = await c.env.R2_BUCKET.get(r2Key.chunks(author, title));
	if (!obj) {
		return notFound(`Book not found: ${author}/${title}`);
	}

	const data: ChunksData = await obj.json();
	const chapter = data.chapters.find((ch) => ch.number === chapterNum);
	if (!chapter) {
		return notFound(`Chapter ${chapterNum} not found in ${author}/${title}`);
	}

	const wordCount = chapter.chunks.reduce(
		(sum, chunk) => sum + chunk.split(/\s+/).filter(Boolean).length,
		0,
	);

	return c.json(
		{
			number: chapter.number,
			title: chapter.title,
			chunks: chapter.chunks,
			word_count: wordCount,
		},
		200,
		{ "Cache-Control": CACHE_IMMUTABLE },
	);
});

export default app;
