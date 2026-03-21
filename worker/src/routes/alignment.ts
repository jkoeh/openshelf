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

	const obj = await c.env.R2_BUCKET.get(r2Key.alignment(author, title));
	if (!obj) {
		return notFound(`Alignment not found: ${author}/${title}`);
	}

	const data = await obj.json();
	return c.json(data, 200, { "Cache-Control": CACHE_IMMUTABLE });
});

export default app;
