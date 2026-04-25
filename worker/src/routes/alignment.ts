import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { ChapterNumberStringSchema, SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const BookParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
});

const ChapterParamsSchema = BookParamsSchema.extend({
	chapter: ChapterNumberStringSchema.openapi({
		param: { name: "chapter", in: "path" },
		example: "1",
	}),
});

const WordEntrySchema = z
	.object({
		word: z.string(),
		start: z.number(),
		end: z.number(),
	})
	.passthrough();

const AlignmentChapterSchema = z
	.object({
		chapter: z.number().int(),
		words: z.array(WordEntrySchema),
	})
	.openapi("AlignmentChapter");

const AlignmentDataSchema = z
	.object({
		version: z.number().int(),
		rendition: z.string(),
		chapters: z.array(
			z
				.object({
					number: z.number().int().optional(),
					chapter: z.number().int().optional(),
					words: z.array(WordEntrySchema),
				})
				.passthrough(),
		),
	})
	.openapi("AlignmentData");

interface AlignmentChapter {
	number?: number;
	chapter?: number;
	words: unknown[];
}

interface AlignmentData {
	version: number;
	rendition: string;
	chapters: AlignmentChapter[];
}

async function fetchAlignment(
	bucket: R2Bucket,
	author: string,
	title: string,
): Promise<AlignmentData | null> {
	const obj = await bucket.get(r2Key.alignment(author, title));
	if (!obj) return null;
	return (await obj.json()) as AlignmentData;
}

const fullRoute = createRoute({
	method: "get",
	path: "/",
	tags: ["alignment"],
	summary: "Get the full WhisperX word alignment (legacy / opt-in)",
	request: { params: BookParamsSchema },
	responses: {
		200: {
			description: "Alignment data for all chapters",
			content: { "application/json": { schema: AlignmentDataSchema } },
		},
		400: {
			description: "Invalid slug",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Alignment not found (book not processed with --whisperx)",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const chapterRoute = createRoute({
	method: "get",
	path: "/{chapter}",
	tags: ["alignment"],
	summary: "Get word alignment for a single chapter (legacy / opt-in)",
	request: { params: ChapterParamsSchema },
	responses: {
		200: {
			description: "Per-chapter alignment",
			content: { "application/json": { schema: AlignmentChapterSchema } },
		},
		400: {
			description: "Invalid slug or chapter number",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Alignment or chapter not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(chapterRoute, async (c) => {
	const { author, title, chapter } = c.req.valid("param");

	const data = await fetchAlignment(c.env.R2_BUCKET, author, title);
	if (!data) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: `Alignment not found: ${author}/${title}` } },
			404,
		);
	}

	const chapterNum = Number.parseInt(chapter, 10);
	const chapterData = data.chapters.find((ch) => (ch.chapter ?? ch.number) === chapterNum);
	if (!chapterData) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: `Alignment for chapter ${chapter} not found` } },
			404,
		);
	}

	return c.json(
		{
			chapter: chapterNum,
			words: chapterData.words as z.infer<typeof WordEntrySchema>[],
		},
		200,
		{ "Cache-Control": CACHE_IMMUTABLE },
	);
});

app.openapi(fullRoute, async (c) => {
	const { author, title } = c.req.valid("param");

	const data = await fetchAlignment(c.env.R2_BUCKET, author, title);
	if (!data) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: `Alignment not found: ${author}/${title}` } },
			404,
		);
	}

	return c.json(data as z.infer<typeof AlignmentDataSchema>, 200, {
		"Cache-Control": CACHE_IMMUTABLE,
	});
});

export default app;
