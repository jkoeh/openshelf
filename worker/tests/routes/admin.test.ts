import { env } from "cloudflare:test";
import { SignJWT, exportJWK, generateKeyPair } from "jose";
import { afterEach, describe, expect, it, vi } from "vitest";
import app from "../../src/index";
import type { Env } from "../../src/types";

const clientId = "openshelf-test.apps.googleusercontent.com";
const limiter = { limit: async () => ({ success: true }) } as RateLimit;
const bindings: Env = {
	R2_BUCKET: env.R2_BUCKET,
	JOB_DB: env.JOB_DB,
	OWNER_TOKEN: "test-owner-token-longer-than-twenty-four",
	PC_TOKEN: "test-consumer-token-longer-than-twenty-four",
	GOOGLE_CLIENT_ID: clientId,
	SEARCH_RATE_LIMITER: limiter,
	CREATE_RATE_LIMITER: limiter,
	AUTH_RATE_LIMITER: limiter,
};

afterEach(() => vi.unstubAllGlobals());

describe("signed Google owner route authorization", () => {
	it("admits the exact owner for admin identity and cancel while rejecting another account", async () => {
		const { privateKey, publicKey } = await generateKeyPair("RS256");
		const jwk = { ...(await exportJWK(publicKey)), kid: "test-key", alg: "RS256", use: "sig" };
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => Response.json({ keys: [jwk] })),
		);
		const sign = (email: string) =>
			new SignJWT({ email, email_verified: true })
				.setProtectedHeader({ alg: "RS256", kid: "test-key" })
				.setIssuer("https://accounts.google.com")
				.setAudience(clientId)
				.setSubject("google-user-id")
				.setIssuedAt()
				.setExpirationTime("1h")
				.sign(privateKey);
		const owner = await sign("johnkoeh@gmail.com");
		const other = await sign("someone@example.com");
		const call = (path: string, token: string, method = "GET") =>
			app.request(
				`https://test.example/api/v1${path}`,
				{ method, headers: { Authorization: `Bearer ${token}` } },
				bindings,
			);
		const me = await call("/admin/me", owner);
		expect(me.status).toBe(200);
		expect(await me.json()).toEqual({ email: "johnkoeh@gmail.com" });
		expect((await call("/admin/me", "test-owner-token-longer-than-twenty-four")).status).toBe(401);
		expect((await call("/admin/me", other)).status).toBe(401);

		const timestamp = new Date().toISOString();
		const jobId = crypto.randomUUID();
		const startId = crypto.randomUUID();
		await env.JOB_DB.prepare(
			"INSERT INTO source_books(source_id,title,author,epub_url,updated_at) VALUES(?,?,?,?,?)",
		)
			.bind(
				"gutenberg:11111",
				"Test",
				"Author",
				"https://www.gutenberg.org/ebooks/11111.epub3.images",
				timestamp,
			)
			.run();
		await env.JOB_DB.prepare("INSERT INTO generation_starts(id,day,created_at) VALUES(?,?,?)")
			.bind(startId, timestamp.slice(0, 10), timestamp)
			.run();
		await env.JOB_DB.prepare(`INSERT INTO generation_jobs(id,source_id,build_id,start_id,state,stage,created_at,updated_at)
			VALUES(?,?,?,?,'queued','queued',?,?)`)
			.bind(jobId, "gutenberg:11111", "0123456789abcdef", startId, timestamp, timestamp)
			.run();
		const path = `/generation-jobs/${jobId}/cancel`;
		expect((await call(path, other, "POST")).status).toBe(401);
		expect((await call(path, owner, "POST")).status).toBe(200);
		const state = await env.JOB_DB.prepare("SELECT state FROM generation_jobs WHERE id=?")
			.bind(jobId)
			.first<{ state: string }>();
		expect(state?.state).toBe("canceled");
	});
});
