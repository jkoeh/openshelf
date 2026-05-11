import { swaggerUI } from "@hono/swagger-ui";
import { cors } from "./middleware/cors";
import audio from "./routes/audio";
import book from "./routes/book";
import catalog from "./routes/catalog";
import chapters from "./routes/chapters";
import cover from "./routes/cover";
import epub from "./routes/epub";
import health from "./routes/health";
import type { Env } from "./types";
import { createOpenAPIApp } from "./utils/openapi-app";
import { errorResponse } from "./utils/response";

const app = createOpenAPIApp<{ Bindings: Env }>();

app.use("*", cors);

app.route("/api/v1/health", health);
app.route("/api/v1/catalog", catalog);
app.route("/api/v1/books/:author/:title/chapters/:number", chapters);
app.route("/api/v1/books/:author/:title/audio/:chapter", audio);
app.route("/api/v1/books/:author/:title/cover", cover);
app.route("/api/v1/books/:author/:title/epub", epub);
app.route("/api/v1/books/:author/:title", book);

app.doc("/api/v1/openapi.json", {
	openapi: "3.1.0",
	info: {
		title: "openshelf API",
		version: "0.1.0",
		description:
			"Audiobook catalog + chapter/word/audio data served from R2. Schemas are auto-generated from Zod definitions in src/routes/*.ts.",
	},
	servers: [{ url: "/", description: "this worker" }],
});

app.get("/api/v1/docs", swaggerUI({ url: "/api/v1/openapi.json" }));

app.onError((err, c) => {
	console.error(err);
	return errorResponse("INTERNAL_ERROR", "An unexpected error occurred", 500);
});

app.notFound((c) => {
	return errorResponse("NOT_FOUND", `Not found: ${c.req.path}`, 404);
});

export default app;
