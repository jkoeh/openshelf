import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_NO_STORE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
});

const ManifestSectionSchema = z.object({
	sequence: z.number().int().positive(),
	section_type: z.string(),
	ordinal: z.number().int().positive().nullable(),
	display_label: z.string(),
	display_title: z.string(),
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
	version: z.literal(2),
	build: z.string(),
	rendition: z.string(),
	voice: z.string(),
	engine: z.string(),
	pipeline_version: z.string(),
	total_duration_seconds: z.number(),
	section_count: z.number().int().nonnegative(),
	sections: z.array(ManifestSectionSchema),
});

const BuildOptionSchema = z.object({
	build: z.string(),
	rendition: z.string(),
	voice: z.string(),
	engine: z.string(),
	pipeline_version: z.string(),
	is_current: z.boolean(),
	uploaded_at: z.string().datetime(),
	total_duration_seconds: z.number(),
	section_count: z.number().int(),
	sections: z.array(ManifestSectionSchema),
});

const BookBuildsRenditionSchema = z.object({
	voice: z.string(),
	engine: z.string(),
	display: z.string(),
	current_build: z.string(),
	builds: z.array(BuildOptionSchema),
});

const BookBuildsSchema = z
	.object({
		title: z.string(),
		author: z.string(),
		source: z.string(),
		renditions: z.record(BookBuildsRenditionSchema),
	})
	.openapi("BookBuilds");

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["books"],
	summary: "List retained builds for a book",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "Available rendition/build selections for a book",
			content: { "application/json": { schema: BookBuildsSchema } },
		},
		400: {
			description: "Invalid slug",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Book or retained build manifest not found",
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
	const renditions: z.infer<typeof BookBuildsSchema>["renditions"] = {};

	for (const [rendition, entry] of Object.entries(bookManifest.renditions)) {
		const builds: z.infer<typeof BuildOptionSchema>[] = [];
		const retainedBuilds = [...new Set(entry.available_builds)];

		for (const build of retainedBuilds) {
			const renditionManifestObj = await c.env.R2_BUCKET.get(
				r2Key.renditionManifest(author, title, rendition, build),
			);
			if (!renditionManifestObj) {
				return c.json(
					{
						error: {
							code: "NOT_FOUND",
							message: `Build manifest not found: ${author}/${title}/${rendition}/${build}`,
						},
					},
					404,
				);
			}

			const parsed = RenditionManifestSchema.safeParse(await renditionManifestObj.json());
			if (!parsed.success) {
				return c.json(
					{
						error: {
							code: "NOT_FOUND",
							message: `Incompatible audiobook build: ${author}/${title}/${rendition}/${build}`,
						},
					},
					404,
				);
			}
			const renditionManifest = parsed.data;
			builds.push({
				build: renditionManifest.build,
				rendition: renditionManifest.rendition,
				voice: renditionManifest.voice,
				engine: renditionManifest.engine,
				pipeline_version: renditionManifest.pipeline_version,
				is_current: build === entry.current_build,
				uploaded_at: renditionManifestObj.uploaded.toISOString(),
				total_duration_seconds: renditionManifest.total_duration_seconds,
				section_count: renditionManifest.sections.length,
				sections: renditionManifest.sections,
			});
		}

		renditions[rendition] = {
			voice: entry.voice,
			engine: entry.engine,
			display: entry.display,
			current_build: entry.current_build,
			builds,
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
		{ "Cache-Control": CACHE_NO_STORE },
	);
});

export default app;
