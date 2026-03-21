import { Hono } from "hono";
import { CACHE_IMMUTABLE } from "../constants";
import type { Env } from "../types";
import { r2Key } from "../utils/r2-keys";
import { badRequest, notFound } from "../utils/response";
import { isValidSlug } from "../utils/validation";

const app = new Hono<{ Bindings: Env }>();

app.get("/", async (c) => {
	const author = c.req.param("author");
	const title = c.req.param("title");

	if (!isValidSlug(author) || !isValidSlug(title)) {
		return badRequest("Invalid author or title slug");
	}

	const obj = await c.env.R2_BUCKET.get(r2Key.epub(author, title));
	if (!obj) {
		return notFound(`EPUB not found: ${author}/${title}`);
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
