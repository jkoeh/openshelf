import { createRoute, z } from "@hono/zod-openapi";
import type { Env } from "../types";
import { createOpenAPIApp } from "../utils/openapi-app";

const HealthResponseSchema = z
	.object({
		status: z.literal("ok"),
		version: z.string().openapi({ example: "0.1.0" }),
	})
	.openapi("Health");

const route = createRoute({
	method: "get",
	path: "/",
	tags: ["health"],
	summary: "Liveness probe",
	responses: {
		200: {
			description: "Worker is up",
			content: { "application/json": { schema: HealthResponseSchema } },
		},
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, (c) => {
	return c.json({ status: "ok" as const, version: "0.1.0" }, 200);
});

export default app;
