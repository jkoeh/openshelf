import { createMiddleware } from "hono/factory";
import type { Env } from "../types";

const CORS_HEADERS = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Allow-Methods": "GET, OPTIONS",
	"Access-Control-Allow-Headers": "Content-Type, Range",
	"Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
	"Access-Control-Max-Age": "86400",
};

export const cors = createMiddleware<{ Bindings: Env }>(async (c, next) => {
	if (c.req.method === "OPTIONS") {
		return new Response(null, { status: 204, headers: CORS_HEADERS });
	}
	await next();
	for (const [key, value] of Object.entries(CORS_HEADERS)) {
		c.res.headers.set(key, value);
	}
});
