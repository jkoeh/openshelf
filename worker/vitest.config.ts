import { resolve } from "node:path";
import { defineWorkersConfig, readD1Migrations } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig(async () => ({
	test: {
		globals: true,
		setupFiles: ["./tests/apply-migrations.ts"],
		poolOptions: {
			workers: {
				wrangler: { configPath: "./wrangler.toml" },
				miniflare: {
					bindings: {
						TEST_MIGRATIONS: await readD1Migrations(resolve("migrations")),
						OWNER_TOKEN: "test-owner-token-longer-than-twenty-four",
						PC_TOKEN: "test-consumer-token-longer-than-twenty-four",
					},
				},
			},
		},
	},
}));
