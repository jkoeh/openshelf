import { Hono } from "hono";
import { CACHE_IMMUTABLE } from "../constants";
import type { Env } from "../types";
import { r2Key } from "../utils/r2-keys";
import { notFound } from "../utils/response";

const app = new Hono<{ Bindings: Env }>();

app.get("/", async (c) => {
	const author = c.req.param("author");
	const title = c.req.param("title");
	if (!author || !title) return notFound("Missing params");

	// Try jpg first, then png
	let obj = await c.env.R2_BUCKET.get(r2Key.cover(author, title, "jpg"));
	let contentType = "image/jpeg";

	if (!obj) {
		obj = await c.env.R2_BUCKET.get(r2Key.cover(author, title, "png"));
		contentType = "image/png";
	}

	if (!obj) return notFound("Cover not found");

	return new Response(obj.body, {
		headers: {
			"Content-Type": contentType,
			"Cache-Control": CACHE_IMMUTABLE,
		},
	});
});

export default app;
