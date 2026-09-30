import type { Env } from "../src/types";

declare module "cloudflare:test" {
	interface ProvidedEnv extends Env {}
	interface ProvidedEnv {
		TEST_MIGRATIONS: D1Migration[];
		JOB_DB: D1Database;
		OWNER_TOKEN: string;
		PC_TOKEN: string;
		SEARCH_RATE_LIMITER: RateLimit;
		AUTH_RATE_LIMITER: RateLimit;
	}
}
