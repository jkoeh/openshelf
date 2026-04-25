import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { ChapterNumberStringSchema, SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
	chapter: ChapterNumberStringSchema.openapi({
		param: { name: "chapter", in: "path" },
		example: "01",
	}),
});

const HeadersSchema = z.object({
	range: z
		.string()
		.optional()
		.openapi({ description: "Standard HTTP Range header (bytes=start-end)" }),
});

const BinaryAudioContent = {
	"audio/mp4": { schema: { type: "string", format: "binary" } as const },
	"audio/mpeg": { schema: { type: "string", format: "binary" } as const },
};

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["audio"],
	summary: "Stream a chapter's audio (m4a / mp3 fallback)",
	request: { params: ParamsSchema, headers: HeadersSchema },
	responses: {
		200: {
			description: "Full audio stream",
			headers: {
				"Content-Length": { schema: { type: "string" } },
				"Accept-Ranges": { schema: { type: "string" } },
			},
			content: BinaryAudioContent,
		},
		206: {
			description: "Partial content (Range request)",
			headers: {
				"Content-Range": { schema: { type: "string" } },
				"Content-Length": { schema: { type: "string" } },
			},
			content: BinaryAudioContent,
		},
		400: {
			description: "Invalid params",
			content: { "application/json": { schema: ErrorSchema } },
		},
		404: {
			description: "Audio not found",
			content: { "application/json": { schema: ErrorSchema } },
		},
		416: {
			description: "Invalid Range header",
			content: { "application/json": { schema: ErrorSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
	const { author, title, chapter } = c.req.valid("param");
	const paddedChapter = chapter.padStart(2, "0");
	const rangeHeader = c.req.header("Range");

	let range: R2Range | undefined;
	if (rangeHeader) {
		const match = rangeHeader.match(/^bytes=(\d+)-(\d*)$/);
		if (!match) {
			return c.json(
				{ error: { code: "RANGE_NOT_SATISFIABLE", message: "Invalid Range header" } },
				416,
			);
		}
		const offset = Number(match[1]);
		const end = match[2] ? Number(match[2]) : undefined;
		range = end !== undefined ? { offset, length: end - offset + 1 } : { offset };
	}

	// Try .m4a first, fall back to .mp3 for legacy content
	const m4aKey = r2Key.audio(author, title, paddedChapter);
	let obj = await c.env.R2_BUCKET.get(m4aKey, range ? { range } : undefined);
	let contentType = "audio/mp4";

	if (!obj) {
		const mp3Key = m4aKey.replace(/\.m4a$/, ".mp3");
		obj = await c.env.R2_BUCKET.get(mp3Key, range ? { range } : undefined);
		contentType = "audio/mpeg";
	}

	if (!obj) {
		return c.json(
			{
				error: {
					code: "NOT_FOUND",
					message: `Audio not found: ${author}/${title} chapter ${chapter}`,
				},
			},
			404,
		);
	}

	const headers: Record<string, string> = {
		"Content-Type": contentType,
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
