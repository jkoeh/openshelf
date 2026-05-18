import { describe, expect, it } from "vitest";
import { r2Key } from "../../src/utils/r2-keys";

const AUTHOR = "franz-kafka";
const TITLE = "the-metamorphosis";
const RENDITION = "kokoro-af-heart";
const BUILD = "2a4f9c1b3d8e7f60";

describe("r2Key", () => {
	it("builds book-level keys", () => {
		expect(r2Key.bookManifest(AUTHOR, TITLE)).toBe(
			"books/franz-kafka/the-metamorphosis/manifest.json",
		);
		expect(r2Key.epub(AUTHOR, TITLE)).toBe("books/franz-kafka/the-metamorphosis/book.epub");
		expect(r2Key.cover(AUTHOR, TITLE)).toBe("books/franz-kafka/the-metamorphosis/cover.jpg");
	});

	it("builds the per-build prefix", () => {
		expect(r2Key.buildPrefix(AUTHOR, TITLE, RENDITION, BUILD)).toBe(
			"books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1b3d8e7f60",
		);
	});

	it("builds build-scoped artifact keys", () => {
		expect(r2Key.audio(AUTHOR, TITLE, RENDITION, BUILD, 1)).toBe(
			"books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1b3d8e7f60/chapter-01.m4a",
		);
		expect(r2Key.chapterData(AUTHOR, TITLE, RENDITION, BUILD)).toBe(
			"books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1b3d8e7f60/chapter_data.json",
		);
		expect(r2Key.renditionManifest(AUTHOR, TITLE, RENDITION, BUILD)).toBe(
			"books/franz-kafka/the-metamorphosis/audio/kokoro-af-heart/builds/2a4f9c1b3d8e7f60/rendition-manifest.json",
		);
	});
});
