import { Hono } from "hono";
import { cors } from "./middleware/cors";
import alignment from "./routes/alignment";
import audio from "./routes/audio";
import book from "./routes/book";
import catalog from "./routes/catalog";
import chapters from "./routes/chapters";
import cover from "./routes/cover";
import epub from "./routes/epub";
import health from "./routes/health";
import type { Env } from "./types";
import { errorResponse } from "./utils/response";

const app = new Hono<{ Bindings: Env }>();

app.use("*", cors);

app.route("/api/v1/health", health);
app.route("/api/v1/catalog", catalog);
app.route("/api/v1/books/:author/:title/chapters/:number", chapters);
app.route("/api/v1/books/:author/:title/audio/:chapter", audio);
app.route("/api/v1/books/:author/:title/cover", cover);
app.route("/api/v1/books/:author/:title/epub", epub);
app.route("/api/v1/books/:author/:title/alignment", alignment);
app.route("/api/v1/books/:author/:title", book);

app.onError((err, c) => {
	console.error(err);
	return errorResponse("INTERNAL_ERROR", "An unexpected error occurred", 500);
});

app.notFound((c) => {
	return errorResponse("NOT_FOUND", `Not found: ${c.req.path}`, 404);
});

export default app;
