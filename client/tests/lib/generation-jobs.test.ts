import { describe, expect, it } from "vitest";
import { latestJob } from "../../lib/generation-jobs";
import type { GenerationJob, SourceBook } from "../../types";

const book: SourceBook = {
	source_id: "gutenberg:11",
	title: "Alice",
	author: "Lewis Carroll",
	state: "available",
	job_state: "failed",
	job_id: "new-job",
	job_updated_at: "2026-09-30T10:00:00.000Z",
	author_slug: "lewis-carroll",
	title_slug: "alice-g11",
};
const cached: GenerationJob = {
	id: "old-job",
	source_id: book.source_id,
	state: "failed",
	stage: "failed",
	author_slug: null,
	title_slug: null,
	error_code: null,
	created_at: "2026-09-29T10:00:00.000Z",
	updated_at: "2026-09-29T10:00:00.000Z",
};

describe("latest job selection", () => {
	it("uses a newer source suggestion instead of an older in-memory job", () => {
		expect(latestJob(book, cached)).toBeNull();
		expect(latestJob(book, { ...cached, id: "new-job" })).toBeNull();
	});

	it("keeps a new local request while the short-lived search cache is stale", () => {
		const local = { ...cached, updated_at: "2026-09-30T10:00:01.000Z" };
		expect(latestJob(book, local)).toBe(local);
	});

	it("retains a matching status response and handles old search responses", () => {
		const match = { ...cached, id: "new-job", updated_at: "2026-09-30T10:00:00.000Z" };
		expect(latestJob(book, match)).toBe(match);
		expect(latestJob({ ...book, job_updated_at: undefined }, cached)).toBe(cached);
	});
});
