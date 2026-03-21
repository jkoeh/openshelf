import { Hono } from "hono";
import { CACHE_IMMUTABLE } from "../constants";
import type { Env } from "../types";
import { r2Key } from "../utils/r2-keys";
import { badRequest, errorResponse, notFound } from "../utils/response";
import { isValidChapter, isValidSlug } from "../utils/validation";

const app = new Hono<{ Bindings: Env }>();

app.get("/", async (c) => {
	const author = c.req.param("author");
	const title = c.req.param("title");
	const chapter = c.req.param("chapter");

	if (!isValidSlug(author) || !isValidSlug(title)) {
		return badRequest("Invalid author or title slug");
	}
	if (!isValidChapter(chapter)) {
		return badRequest("Invalid chapter number");
	}

	const key = r2Key.audio(author, title, chapter.padStart(2, "0"));
	const rangeHeader = c.req.header("Range");

	let range: R2Range | undefined;
	if (rangeHeader) {
		const match = rangeHeader.match(/^bytes=(\d+)-(\d*)$/);
		if (!match) {
			return errorResponse("RANGE_NOT_SATISFIABLE", "Invalid Range header", 416);
		}
		const offset = Number(match[1]);
		const end = match[2] ? Number(match[2]) : undefined;
		range = end !== undefined ? { offset, length: end - offset + 1 } : { offset };
	}

	const obj = await c.env.R2_BUCKET.get(key, range ? { range } : undefined);
	if (!obj) {
		return notFound(`Audio not found: ${author}/${title} chapter ${chapter}`);
	}

	const headers: Record<string, string> = {
		"Content-Type": "audio/ogg",
		"Accept-Ranges": "bytes",
		"Cache-Control": CACHE_IMMUTABLE,
		"Content-Disposition": "inline",
	};

	if (rangeHeader && obj.range) {
		const r = obj.range as { offset: number; length: number };
		const end = r.offset + r.length - 1;
		headers["Content-Range"] = `bytes ${r.offset}-${end}/${obj.size}`;
		headers["Content-Length"] = String(r.length);
		return new Response(obj.body, { status: 206, headers });
	}

	headers["Content-Length"] = String(obj.size);
	return new Response(obj.body, { status: 200, headers });
});

export default app;
