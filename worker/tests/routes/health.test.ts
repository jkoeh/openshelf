import { env, createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import app from "../../src/index";

describe("GET /api/v1/health", () => {
	it("returns status ok", async () => {
		const res = await app.request("/api/v1/health", {}, env);
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(body.status).toBe("ok");
		expect(body.version).toBeDefined();
	});

	it("includes CORS headers", async () => {
		const res = await app.request("/api/v1/health", {}, env);
		expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
	});
});
