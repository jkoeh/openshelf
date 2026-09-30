export type Env = {
	R2_BUCKET: R2Bucket;
	JOB_DB?: D1Database;
	SEARCH_RATE_LIMITER?: RateLimit;
	AUTH_RATE_LIMITER?: RateLimit;
	OWNER_TOKEN?: string;
	PC_TOKEN?: string;
};
