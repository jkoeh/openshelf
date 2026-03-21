import { createMiddleware } from "hono/factory";
import type { Env } from "../types";

export const cors = createMiddleware<{ Bindings: Env }>(async (c, next) => {
	await next();
	c.res.headers.set("Access-Control-Allow-Origin", "*");
	c.res.headers.set("Access-Control-Allow-Methods", "GET, OPTIONS");
	c.res.headers.set("Access-Control-Allow-Headers", "Content-Type, Range");
	c.res.headers.set(
		"Access-Control-Expose-Headers",
		"Content-Range, Accept-Ranges, Content-Length",
	);
	c.res.headers.set("Access-Control-Max-Age", "86400");
});
