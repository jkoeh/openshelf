/**
 * Seed local miniflare R2 with fixtures for development.
 *
 * Usage: npm run seed (after `npm run dev` is running in another terminal)
 */

import { execSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WORKER_DIR = join(__dirname, "..");
const FIXTURES_DIR = join(WORKER_DIR, "fixtures");
const BASE_URL = "http://localhost:8787";

const AUTHOR = "franz-kafka";
const TITLE = "the-trial";
const RENDITION = "kokoro-af-heart";

const seeds: Array<{ key: string; file: string }> = [
	{ key: "catalog.json", file: "catalog.json" },
	{ key: `books/${AUTHOR}/${TITLE}/audio/${RENDITION}/manifest.json`, file: "manifest.json" },
	{
		key: `books/${AUTHOR}/${TITLE}/audio/${RENDITION}/word_alignment.json`,
		file: "word_alignment.json",
	},
	{
		key: `books/${AUTHOR}/${TITLE}/audio/${RENDITION}/chapter-01.m4a`,
		file: "chapter-01.m4a",
	},
];

function seed() {
	console.log("Seeding local R2 via wrangler r2 object put...\n");

	for (const { key, file } of seeds) {
		const filePath = join(FIXTURES_DIR, file);
		execSync(`npx wrangler r2 object put "openshelf/${key}" --file "${filePath}" --local`, {
			stdio: "inherit",
			cwd: WORKER_DIR,
		});
		console.log(`  PUT ${key} <- ${file}`);
	}

	console.log("\nDone! Fixtures seeded into local R2.");
	console.log(`Try: curl ${BASE_URL}/api/v1/health`);
	console.log(`Try: curl ${BASE_URL}/api/v1/catalog`);
}

seed();
