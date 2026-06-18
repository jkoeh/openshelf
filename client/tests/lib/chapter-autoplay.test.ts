import { describe, expect, it } from "vitest";
import {
	chapterPlaybackKey,
	nextAutoplayChapterAfterFinish,
	shouldPlayPendingChapter,
} from "../../lib/chapter-autoplay";

describe("chapter autoplay", () => {
	it("requests the next chapter when the current pinned chapter finishes", () => {
		const transition = nextAutoplayChapterAfterFinish({
			didJustFinish: true,
			currentChapter: 2,
			totalChapters: 4,
			pinnedChapter: 2,
			rendition: "kokoro-af-heart",
			build: "2a4f9c1b3d8e7f60",
			handledFinishKey: null,
			pendingAutoplayChapter: null,
		});

		expect(transition).toEqual({
			nextChapter: 3,
			pendingAutoplayChapter: 3,
			handledFinishKey: "kokoro-af-heart:2a4f9c1b3d8e7f60:2",
		});
	});

	it("does not advance again while the same finished source is still reported", () => {
		const handledFinishKey = chapterPlaybackKey(2, "kokoro-af-heart", "2a4f9c1b3d8e7f60");

		expect(
			nextAutoplayChapterAfterFinish({
				didJustFinish: true,
				currentChapter: 2,
				totalChapters: 4,
				pinnedChapter: 2,
				rendition: "kokoro-af-heart",
				build: "2a4f9c1b3d8e7f60",
				handledFinishKey,
				pendingAutoplayChapter: null,
			}),
		).toBeNull();
	});

	it("does not process a stale finish while a chapter is waiting to autoplay", () => {
		expect(
			nextAutoplayChapterAfterFinish({
				didJustFinish: true,
				currentChapter: 3,
				totalChapters: 4,
				pinnedChapter: 3,
				rendition: "kokoro-af-heart",
				build: "2a4f9c1b3d8e7f60",
				handledFinishKey: "kokoro-af-heart:2a4f9c1b3d8e7f60:2",
				pendingAutoplayChapter: 3,
			}),
		).toBeNull();
	});

	it("waits until the pending chapter has a loaded audio source", () => {
		expect(
			shouldPlayPendingChapter({
				pendingAutoplayChapter: 3,
				currentChapter: 3,
				pinnedChapter: 2,
				isLoaded: true,
				hasAudioSource: false,
			}),
		).toBe(false);

		expect(
			shouldPlayPendingChapter({
				pendingAutoplayChapter: 3,
				currentChapter: 3,
				pinnedChapter: 3,
				isLoaded: true,
				hasAudioSource: true,
			}),
		).toBe(true);
	});
});
