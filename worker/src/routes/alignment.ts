import { Hono } from "hono";
import { CACHE_IMMUTABLE } from "../constants";
import type { Env } from "../types";
import { r2Key } from "../utils/r2-keys";
import { badRequest, notFound } from "../utils/response";
import { isValidChapter, isValidSlug } from "../utils/validation";

interface AlignmentChapter {
	chapter: number;
	words: unknown[];
}

interface AlignmentData {
	version: number;
	rendition: string;
	chapters: AlignmentChapter[];
}

const app = new Hono<{ Bindings: Env }>();

async function fetchAlignment(c: { env: { R2_BUCKET: R2Bucket } }, author: string, title: string) {
	const obj = await c.env.R2_BUCKET.get(r2Key.alignment(author, title));
	if (!obj) return null;
	return (await obj.json()) as AlignmentData;
}

app.get("/:chapter", async (c) => {
	const author = c.req.param("author");
	const title = c.req.param("title");
	const chapter = c.req.param("chapter");

	if (!isValidSlug(author) || !isValidSlug(title)) {
		return badRequest("Invalid author or title slug");
	}
	if (!isValidChapter(chapter)) {
		return badRequest("Invalid chapter number");
	}

	const data = await fetchAlignment(c, author, title);
	if (!data) {
		return notFound(`Alignment not found: ${author}/${title}`);
	}

	const chapterNum = Number.parseInt(chapter, 10);
	const chapterData = data.chapters.find((ch) => ch.chapter === chapterNum);
	if (!chapterData) {
		return notFound(`Alignment for chapter ${chapter} not found`);
	}

	return c.json(chapterData, 200, { "Cache-Control": CACHE_IMMUTABLE });
});

app.get("/", async (c) => {
	const author = c.req.param("author");
	const title = c.req.param("title");

	if (!isValidSlug(author) || !isValidSlug(title)) {
		return badRequest("Invalid author or title slug");
	}

	const data = await fetchAlignment(c, author, title);
	if (!data) {
		return notFound(`Alignment not found: ${author}/${title}`);
	}

	return c.json(data, 200, { "Cache-Control": CACHE_IMMUTABLE });
});

export default app;
