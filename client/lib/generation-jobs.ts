import type { GenerationJob, SourceBook } from "../types";

export function latestJob(
	book: SourceBook,
	cached: GenerationJob | undefined,
): GenerationJob | null {
	if (!cached) return null;
	if (!book.job_updated_at) return cached;
	if (cached.updated_at > book.job_updated_at) return cached;
	return cached.id === book.job_id && cached.updated_at === book.job_updated_at ? cached : null;
}
