/**
 * Seed local miniflare R2 with fixtures for development.
 *
 * Usage: npm run seed (after `npm run dev` is running in another terminal)
 *
 * This uses the wrangler unstable_dev API to put objects into local R2.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const FIXTURES_DIR = join(import.meta.dirname, "..", "fixtures");
const BASE_URL = "http://localhost:8787";

const AUTHOR = "franz-kafka";
const TITLE = "the-trial";
const RENDITION = "kokoro-af-heart";

const seeds: Array<{ key: string; file: string; contentType: string }> = [
	{
		key: "catalog.json",
		file: "catalog.json",
		contentType: "application/json",
	},
	{
		key: `books/${AUTHOR}/${TITLE}/audio/${RENDITION}/manifest.json`,
		file: "manifest.json",
		contentType: "application/json",
	},
	{
		key: `books/${AUTHOR}/${TITLE}/chunks.json`,
		file: "chunks.json",
		contentType: "application/json",
	},
	{
		key: `books/${AUTHOR}/${TITLE}/audio/${RENDITION}/word_alignment.json`,
		file: "word_alignment.json",
		contentType: "application/json",
	},
];

async function seed() {
	console.log("Seeding local R2 via wrangler r2 object put...\n");

	for (const { key, file } of seeds) {
		const filePath = join(FIXTURES_DIR, file);
		const args = ["npx", "wrangler", "r2", "object", "put", `openshelf/${key}`, "--file", filePath, "--local"];
		const proc = Bun?.spawn?.(args) ?? null;
		if (!proc) {
			// Fallback: use child_process
			const { execSync } = await import("node:child_process");
			execSync(`npx wrangler r2 object put "openshelf/${key}" --file "${filePath}" --local`, {
				stdio: "inherit",
				cwd: join(import.meta.dirname, ".."),
			});
		}
		console.log(`  PUT ${key} <- ${file}`);
	}

	console.log("\nDone! Fixtures seeded into local R2.");
	console.log(`Try: curl ${BASE_URL}/api/v1/health`);
	console.log(`Try: curl ${BASE_URL}/api/v1/catalog`);
}

seed().catch(console.error);
