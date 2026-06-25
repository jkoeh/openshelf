import { describe, expect, it } from "vitest";
import {
	nextAutoplaySectionAfterFinish,
	sectionPlaybackKey,
	shouldPlayPendingSection,
} from "../../lib/chapter-autoplay";

describe("section autoplay", () => {
	it("requests the next section when the current pinned section finishes", () => {
		const transition = nextAutoplaySectionAfterFinish({
			didJustFinish: true,
			currentSection: 2,
			totalSections: 4,
			pinnedSection: 2,
			rendition: "kokoro-af-heart",
			build: "2a4f9c1b3d8e7f60",
			handledFinishKey: null,
			pendingAutoplaySection: null,
		});

		expect(transition).toEqual({
			nextSection: 3,
			pendingAutoplaySection: 3,
			handledFinishKey: "kokoro-af-heart:2a4f9c1b3d8e7f60:2",
		});
	});

	it("does not advance again while the same finished source is still reported", () => {
		const handledFinishKey = sectionPlaybackKey(2, "kokoro-af-heart", "2a4f9c1b3d8e7f60");

		expect(
			nextAutoplaySectionAfterFinish({
				didJustFinish: true,
				currentSection: 2,
				totalSections: 4,
				pinnedSection: 2,
				rendition: "kokoro-af-heart",
				build: "2a4f9c1b3d8e7f60",
				handledFinishKey,
				pendingAutoplaySection: null,
			}),
		).toBeNull();
	});

	it("does not process a stale finish while a section is waiting to autoplay", () => {
		expect(
			nextAutoplaySectionAfterFinish({
				didJustFinish: true,
				currentSection: 3,
				totalSections: 4,
				pinnedSection: 3,
				rendition: "kokoro-af-heart",
				build: "2a4f9c1b3d8e7f60",
				handledFinishKey: "kokoro-af-heart:2a4f9c1b3d8e7f60:2",
				pendingAutoplaySection: 3,
			}),
		).toBeNull();
	});

	it("waits until the pending section has a loaded audio source", () => {
		expect(
			shouldPlayPendingSection({
				pendingAutoplaySection: 3,
				currentSection: 3,
				pinnedSection: 2,
				isLoaded: true,
				hasAudioSource: false,
			}),
		).toBe(false);

		expect(
			shouldPlayPendingSection({
				pendingAutoplaySection: 3,
				currentSection: 3,
				pinnedSection: 3,
				isLoaded: true,
				hasAudioSource: true,
			}),
		).toBe(true);
	});
});
