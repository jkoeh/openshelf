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
	tags: ["covers"],
	summary: "Get the cover image (jpg or png)",
	request: { params: ParamsSchema },
	responses: {
		200: {
			description: "Cover image bytes",
			content: {
				"image/jpeg": { schema: { type: "string", format: "binary" } as const },
				"image/png": { schema: { type: "string", format: "binary" } as const },
			},
		},
		400: {
			description: "Invalid slug",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Cover not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { author, title } = c.req.valid("param");

	let obj = await c.env.R2_BUCKET.get(r2Key.cover(author, title, "jpg"));
	let contentType = "image/jpeg";

	if (!obj) {
		obj = await c.env.R2_BUCKET.get(r2Key.cover(author, title, "png"));
		contentType = "image/png";
	}

	if (!obj) {
		return c.json({ error: { code: "NOT_FOUND", message: "Cover not found" } }, 404);
	}

	return new Response(obj.body, {
		headers: {
			"Content-Type": contentType,
			"Cache-Control": CACHE_IMMUTABLE,
		},
	});
});

export default app;
