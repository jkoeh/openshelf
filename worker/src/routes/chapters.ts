import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { BuildSchema, ChapterNumberStringSchema, SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
	number: ChapterNumberStringSchema.openapi({
		param: { name: "number", in: "path" },
		example: "1",
	}),
});

const QuerySchema = z.object({
	rendition: SlugSchema.openapi({ example: "kokoro-af-heart" }),
	build: BuildSchema.openapi({ example: "2a4f9c1b3d8e7f60" }),
});

const WordSchema = z.object({
	word: z.string(),
	start: z.number(),
	end: z.number(),
	chunk_idx: z.number().int().nonnegative(),
});

const ChapterResponseSchema = z
	.object({
		number: z.number().int(),
		title: z.string(),
		chunks: z.array(z.string()),
		word_count: z.number().int(),
		words: z.array(WordSchema),
	})
	.openapi("Chapter");

interface ChapterDataChunk {
	text: string;
	words: { word: string; start: number; end: number }[];
}

interface ChapterDataChapter {
	number: number;
	title: string;
	chunks: ChapterDataChunk[];
	word_count: number;
}

interface ChapterData {
	version: number;
	rendition: string;
	build: string;
	chapters: ChapterDataChapter[];
}

function flattenWords(chunks: ChapterDataChunk[]) {
	const words: z.infer<typeof WordSchema>[] = [];
	for (let i = 0; i < chunks.length; i++) {
		for (const w of chunks[i].words) {
			words.push({ ...w, chunk_idx: i });
		}
	}
	return words;
}

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["chapters"],
	summary: "Get chapter text + word timestamps",
	request: { params: ParamsSchema, query: QuerySchema },
	responses: {
		200: {
			description: "Chapter payload with flattened word timestamps",
			content: { "application/json": { schema: ChapterResponseSchema } },
		},
		400: {
			description: "Invalid slug or chapter number",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Book or chapter not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { author, title, number } = c.req.valid("param");
	const { rendition, build } = c.req.valid("query");
	const chapterNum = Number(number);

	const obj = await c.env.R2_BUCKET.get(r2Key.chapterData(author, title, rendition, build));
	if (!obj) {
		return c.json(
			{
				error: {
					code: "NOT_FOUND",
					message: `Build not found: ${author}/${title}/${rendition}/${build}`,
				},
			},
			404,
		);
	}

	const data = (await obj.json()) as ChapterData;
	const chapter = data.chapters.find((ch) => ch.number === chapterNum);
	if (!chapter) {
		return c.json(
			{
				error: {
					code: "NOT_FOUND",
					message: `Chapter ${chapterNum} not found in ${author}/${title}`,
				},
			},
			404,
		);
	}

	return c.json(
		{
			number: chapter.number,
			title: chapter.title,
			chunks: chapter.chunks.map((ch) => ch.text),
			word_count: chapter.word_count,
			words: flattenWords(chapter.chunks),
		},
		200,
		{ "Cache-Control": CACHE_IMMUTABLE },
	);
});

export default app;
