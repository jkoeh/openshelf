import { applyD1Migrations, env } from "cloudflare:test";
import { beforeAll } from "vitest";

beforeAll(async () => {
	await applyD1Migrations(env.JOB_DB!, env.TEST_MIGRATIONS);
});
