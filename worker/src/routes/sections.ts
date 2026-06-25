import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { BuildSchema, ChapterNumberStringSchema, SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const SectionTypeSchema = z.enum([
	"opening_credits",
	"chapter",
	"prologue",
	"epilogue",
	"epigraph",
	"preface",
	"introduction",
	"afterword",
	"appendix",
	"part",
	"closing_credits",
	"other",
]);

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "lewis-carroll" }),
	title: SlugSchema.openapi({
		param: { name: "title", in: "path" },
		example: "alices-adventures-in-wonderland",
	}),
	sequence: ChapterNumberStringSchema.openapi({
		param: { name: "sequence", in: "path" },
		example: "3",
	}),
});

const QuerySchema = z.object({
	rendition: SlugSchema.openapi({ example: "kokoro-af-heart" }),
	build: BuildSchema.openapi({ example: "2a4f9c1b3d8e7f60" }),
});

const HeadingSchema = z.object({
	display_label: z.string(),
	display_title: z.string(),
	spoken_text: z.string(),
});

const WordSchema = z.object({
	word: z.string(),
	start: z.number(),
	end: z.number(),
	region: z.enum(["heading", "body"]),
	chunk_idx: z.number().int().nonnegative().nullable(),
});

const SectionResponseSchema = z
	.object({
		sequence: z.number().int().positive(),
		section_type: SectionTypeSchema,
		ordinal: z.number().int().positive().nullable(),
		heading: HeadingSchema,
		chunks: z.array(z.string()),
		word_count: z.number().int().nonnegative(),
		words: z.array(WordSchema),
	})
	.openapi("Section");

interface TimedWord {
	word: string;
	start: number;
	end: number;
}

interface SectionDataChunk {
	text: string;
	words: TimedWord[];
}

interface SectionDataSection {
	sequence: number;
	section_type: z.infer<typeof SectionTypeSchema>;
	ordinal: number | null;
	heading: {
		display_label: string;
		display_title: string;
		spoken_text: string;
		words: TimedWord[];
	};
	chunks: SectionDataChunk[];
	word_count: number;
}

interface SectionData {
	version: number;
	rendition: string;
	build: string;
	sections: SectionDataSection[];
}

function flattenWords(section: SectionDataSection): z.infer<typeof WordSchema>[] {
	const words: z.infer<typeof WordSchema>[] = section.heading.words.map((word) => ({
		...word,
		region: "heading",
		chunk_idx: null,
	}));
	for (let index = 0; index < section.chunks.length; index++) {
		for (const word of section.chunks[index].words) {
			words.push({ ...word, region: "body", chunk_idx: index });
		}
	}
	return words;
}

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["sections"],
	summary: "Get section heading, body text, and word timestamps",
	request: { params: ParamsSchema, query: QuerySchema },
	responses: {
		200: {
			description: "Section payload with region-tagged word timestamps",
			content: { "application/json": { schema: SectionResponseSchema } },
		},
		400: {
			description: "Invalid slug or section sequence",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Book, build, or section not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { author, title, sequence } = c.req.valid("param");
	const { rendition, build } = c.req.valid("query");
	const sectionSequence = Number(sequence);

	const object = await c.env.R2_BUCKET.get(r2Key.sectionData(author, title, rendition, build));
	if (!object) {
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

	const data = (await object.json()) as SectionData;
	if (data.version !== 2) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: "Incompatible audiobook build" } },
			404,
		);
	}
	const section = data.sections.find((value) => value.sequence === sectionSequence);
	if (!section) {
		return c.json(
			{
				error: {
					code: "NOT_FOUND",
					message: `Section ${sectionSequence} not found in ${author}/${title}`,
				},
			},
			404,
		);
	}

	return c.json(
		{
			sequence: section.sequence,
			section_type: section.section_type,
			ordinal: section.ordinal,
			heading: {
				display_label: section.heading.display_label,
				display_title: section.heading.display_title,
				spoken_text: section.heading.spoken_text,
			},
			chunks: section.chunks.map((chunk) => chunk.text),
			word_count: section.word_count,
			words: flattenWords(section),
		},
		200,
		{ "Cache-Control": CACHE_IMMUTABLE },
	);
});

export default app;
