import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import { ErrorSchema } from "../schemas/error";
import { BuildSchema, ChapterNumberStringSchema, SlugSchema } from "../schemas/params";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";
import { r2Key } from "../utils/r2-keys";

const ParamsSchema = z.object({
	author: SlugSchema.openapi({ param: { name: "author", in: "path" }, example: "franz-kafka" }),
	title: SlugSchema.openapi({ param: { name: "title", in: "path" }, example: "the-trial" }),
	sequence: ChapterNumberStringSchema.openapi({
		param: { name: "sequence", in: "path" },
		example: "01",
	}),
});

const QuerySchema = z.object({
	rendition: SlugSchema.openapi({ example: "kokoro-af-heart" }),
	build: BuildSchema.openapi({ example: "2a4f9c1b3d8e7f60" }),
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
	summary: "Stream a build-pinned section audio file",
	request: { params: ParamsSchema, query: QuerySchema, headers: HeadersSchema },
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
	const { author, title, sequence } = c.req.valid("param");
	const { rendition, build } = c.req.valid("query");
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
		if (end !== undefined && end < offset) {
			return c.json(
				{ error: { code: "RANGE_NOT_SATISFIABLE", message: "Invalid Range header" } },
				416,
			);
		}
		range = end !== undefined ? { offset, length: end - offset + 1 } : { offset };
	}

	const audioKey = r2Key.audio(author, title, rendition, build, sequence);
	const obj = await c.env.R2_BUCKET.get(audioKey, range ? { range } : undefined);

	if (!obj) {
		return c.json(
			{
				error: {
					code: "NOT_FOUND",
					message: `Audio not found: ${author}/${title} section ${sequence}`,
				},
			},
			404,
		);
	}

	const headers: Record<string, string> = {
		"Content-Type": "audio/mp4",
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
