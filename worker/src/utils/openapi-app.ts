import { OpenAPIHono } from "@hono/zod-openapi";
import type { Env } from "hono";

/**
 * Creates an OpenAPIHono with a defaultHook that converts Zod parse failures
 * into the standard error body shape: { error: { code, message } }.
 *
 * Use this everywhere instead of `new OpenAPIHono(...)` so 400 responses
 * are uniform across routes.
 */
export function createOpenAPIApp<E extends Env>() {
	return new OpenAPIHono<E>({
		defaultHook: (result, c) => {
			if (!result.success) {
				const issue = result.error.issues[0];
				const message = issue?.message ?? "Invalid request";
				return c.json({ error: { code: "INVALID_PARAM", message } }, 400);
			}
		},
	});
}
