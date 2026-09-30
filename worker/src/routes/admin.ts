import { createRoute, z } from "@hono/zod-openapi";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { googleCredentialStatus } from "../utils/google-admin";
import { noStore } from "../utils/job-auth";
import { createOpenAPIApp } from "../utils/openapi-app";

const Me = z.object({ email: z.literal("johnkoeh@gmail.com") }).openapi("AdminIdentity");
const error = { description: "Error", content: { "application/json": { schema: ErrorSchema } } };
const me = createRoute({
	method: "get",
	path: "/me",
	tags: ["admin"],
	summary: "Verify the signed-in Google owner",
	responses: {
		200: { description: "Owner identity", content: { "application/json": { schema: Me } } },
		401: error,
		429: error,
		503: error,
	},
});

const app = createOpenAPIApp<{ Bindings: Env }>();
app.openapi(me, async (c) => {
	if (!c.env.GOOGLE_CLIENT_ID || !c.env.AUTH_RATE_LIMITER)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Admin sign-in is not configured" } },
			503,
			noStore,
		);
	const status = await googleCredentialStatus(c.req.raw, c.env);
	if (status)
		return c.json(
			{
				error: {
					code: status === 429 ? "RATE_LIMITED" : "UNAUTHORIZED",
					message: "Invalid credential or too many attempts",
				},
			},
			status,
			noStore,
		);
	return c.json({ email: "johnkoeh@gmail.com" as const }, 200, noStore);
});

export default app;
