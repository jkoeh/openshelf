import type { GenerationJob } from "../types";

export function audioCreation(job: GenerationJob | null) {
	if (job?.state === "completed")
		return {
			percent: 100,
			verb: "Ready to soar",
			detail: "Your audiobook is ready.",
		};
	if (job?.state === "failed")
		return {
			percent: 0,
			verb: "Taking a breather",
			detail:
				job.error_code === "BOOK_TOO_LONG"
					? "Audio stopped under an earlier word limit. That limit has been removed; the owner can retry. Keep reading in the meantime."
					: "Audio creation stopped. You can keep reading.",
		};
	if (job?.state === "canceled")
		return {
			percent: 0,
			verb: "Resting",
			detail: "Audio creation was canceled. You can keep reading.",
		};
	if (!job || job.state === "queued")
		return {
			percent: 0,
			verb: "Nestling",
			detail: "Waiting for a turn in the narration nest.",
		};
	if (["download", "parse", "direction"].includes(job.stage))
		return {
			percent: 25,
			verb: "Gathering",
			detail: "Gathering pages and warming up our voice.",
		};
	return {
		percent: 50,
		verb: "Hooting",
		detail: "Narrating and polishing your audiobook.",
	};
}
