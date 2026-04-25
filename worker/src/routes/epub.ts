import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
});

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["epub"],
	summary: "Download the annotated EPUB",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "EPUB bytes",
			headers: {
				"Content-Length": { schema: { type: "string" } },
				"Content-Disposition": { schema: { type: "string" } },
			},
			content: {
				"application/epub+zip": {
					schema: { type: "string", format: "binary" } as const,
				},
			},
		},
		400: {
			description: "Invalid slug",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "EPUB not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { author, title } = c.req.valid("param");

	const obj = await c.env.R2_BUCKET.get(r2Key.epub(author, title));
	if (!obj) {
		return c.json(
			{ error: { code: "NOT_FOUND", message: `EPUB not found: ${author}/${title}` } },
			404,
		);
	}

	return new Response(obj.body, {
		headers: {
			"Content-Type": "application/epub+zip",
			"Cache-Control": CACHE_IMMUTABLE,
			"Content-Length": String(obj.size),
			"Content-Disposition": `attachment; filename="${title}.epub"`,
		},
	});
});

export default app;
