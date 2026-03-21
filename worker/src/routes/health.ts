import { Hono } from "hono";
import type { Env } from "../types";

const app = new Hono<{ Bindings: Env }>();

app.get("/", (c) => {
	return c.json({ status: "ok", version: "0.1.0" });
});

export default app;
