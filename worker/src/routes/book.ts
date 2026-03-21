import { Hono } from "hono";
import { CACHE_SHORT } from "../constants";
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

	const obj = await c.env.R2_BUCKET.get(r2Key.manifest(author, title));
	if (!obj) {
		return notFound(`Book not found: ${author}/${title}`);
	}

	const manifest = await obj.json();
	return c.json(manifest, 200, { "Cache-Control": CACHE_SHORT });
});

export default app;
