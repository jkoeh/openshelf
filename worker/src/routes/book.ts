import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_SHORT } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
});

const ManifestChapterSchema = z.object({
	number: z.number().int(),
	title: z.string(),
	filename: z.string(),
	duration_seconds: z.number(),
	word_count: z.number().int(),
});

const BookManifestRenditionSchema = z.object({
	voice: z.string(),
	engine: z.string(),
	display: z.string(),
	current_build: z.string(),
	available_builds: z.array(z.string()),
});

const BookManifestSchema = z.object({
	title: z.string(),
	author: z.string(),
	source: z.string(),
	renditions: z.record(BookManifestRenditionSchema),
});

const RenditionManifestSchema = z.object({
	build: z.string(),
	rendition: z.string(),
	voice: z.string(),
	engine: z.string(),
	pipeline_version: z.string(),
	total_duration_seconds: z.number(),
	chapters: z.array(ManifestChapterSchema),
});

const MergedRenditionSchema = BookManifestRenditionSchema.extend({
	total_duration_seconds: z.number(),
	chapters: z.array(ManifestChapterSchema),
});

const ManifestSchema = z
	.object({
		title: z.string(),
		author: z.string(),
		source: z.string(),
		renditions: z.record(MergedRenditionSchema),
	})
	.openapi("Manifest");

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["books"],
	summary: "Get book manifest",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "Book manifest with chapter list and durations",
			content: { "application/json": { schema: ManifestSchema } },
		},
		400: {
			description: "Invalid slug",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Book not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { author, title } = c.req.valid("param");

	const obj = await c.env.R2_BUCKET.get(r2Key.bookManifest(author, title));
	if (!obj) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: `Book not found: ${author}/${title}` } },
			404,
		);
	}

	const bookManifest = BookManifestSchema.parse(await obj.json());
	const renditions: z.infer<typeof ManifestSchema>["renditions"] = {};

	for (const [rendition, entry] of Object.entries(bookManifest.renditions)) {
		const renditionManifestObj = await c.env.R2_BUCKET.get(
			r2Key.renditionManifest(author, title, rendition, entry.current_build),
		);
		if (!renditionManifestObj) {
			return c.json(
				{
					error: {
						code: "NOT_FOUND",
						message: `Build manifest not found: ${author}/${title}/${rendition}/${entry.current_build}`,
					},
				},
				404,
			);
		}

		const renditionManifest = RenditionManifestSchema.parse(await renditionManifestObj.json());
		renditions[rendition] = {
			...entry,
			total_duration_seconds: renditionManifest.total_duration_seconds,
			chapters: renditionManifest.chapters,
		};
	}

	return c.json(
		{
			title: bookManifest.title,
			author: bookManifest.author,
			source: bookManifest.source,
			renditions,
		},
		200,
		{ "Cache-Control": CACHE_SHORT },
	);
});

export default app;
