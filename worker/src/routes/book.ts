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

const ManifestSchema = z
	.object({
		title: z.string(),
		author: z.string(),
		source: z.string(),
		rendition: z.string(),
		generated_at: z.string().optional(),
		total_duration_seconds: z.number(),
		chapters: z.array(ManifestChapterSchema),
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

	const obj = await c.env.R2_BUCKET.get(r2Key.manifest(author, title));
	if (!obj) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: `Book not found: ${author}/${title}` } },
			404,
		);
	}

	const manifest = (await obj.json()) as z.infer<typeof ManifestSchema>;
	return c.json(manifest, 200, { "Cache-Control": CACHE_SHORT });
});

export default app;
