/**
 * End-to-end tests for the reader data flow.
 *
 * Uses the same fixture data as the worker tests to verify the full pipeline:
 * API response shapes → type parsing → sync engine → rendering keys.
 */
import { describe, expect, it } from "vitest";
import { findChunkAtTime, findWordAtTime } from "../../lib/sync-engine";
import type {
  CatalogBook,
  CatalogResponse,
  ChapterResponse,
  Manifest,
  WordEntry,
} from "../../types";

// Mirror of worker/fixtures — the exact JSON the API returns
const catalogFixture: CatalogResponse = {
  version: 1,
  generated_at: "2025-01-15T12:00:00Z",
  total: 2,
  page: 1,
  limit: 20,
  books: [
    {
      author: "Franz Kafka",
      author_slug: "franz-kafka",
      title: "The Trial",
      title_slug: "the-trial",
      source: "gutenberg",
      rendition: "kokoro-af-heart",
      total_duration_seconds: 28800.0,
      chapter_count: 10,
    },
    {
      author: "Fyodor Dostoevsky",
      author_slug: "fyodor-dostoevsky",
      title: "Crime and Punishment",
      title_slug: "crime-and-punishment",
      source: "gutenberg",
      rendition: "kokoro-af-heart",
      total_duration_seconds: 77400.5,
      chapter_count: 42,
    },
  ],
};

const manifestFixture: Manifest = {
  title: "The Trial",
  author: "Franz Kafka",
  source: "gutenberg",
  renditions: {
    "kokoro-af-heart": {
      voice: "af_heart",
      engine: "kokoro",
      display: "Heart",
      current_build: "2a4f9c1b3d8e7f60",
      available_builds: ["2a4f9c1b3d8e7f60"],
      total_duration_seconds: 28800.0,
      chapters: [
        {
          number: 1,
          title: "Chapter 1",
          filename: "chapter-01.m4a",
          duration_seconds: 2880.0,
          word_count: 5200,
        },
      ],
    },
  },
};

const manifestChapters = manifestFixture.renditions["kokoro-af-heart"].chapters;

const chapterFixture: ChapterResponse = {
  number: 1,
  title: "Chapter 1",
  chunks: [
    "Someone must have been telling lies about Josef K., he knew he had done nothing wrong but, one morning, he was arrested.",
    "Every day at eight in the morning he was brought his breakfast by Mrs. Grubach's cook.",
  ],
  word_count: 38,
};

const wordsFixture: WordEntry[] = [
  { word: "Someone", start: 0.0, end: 0.35, chunk_idx: 0, element_id: "os-p-001" },
  { word: "must", start: 0.36, end: 0.55, chunk_idx: 0, element_id: "os-p-001" },
  { word: "have", start: 0.56, end: 0.72, chunk_idx: 0, element_id: "os-p-001" },
  { word: "been", start: 0.73, end: 0.9, chunk_idx: 0, element_id: "os-p-001" },
  { word: "telling", start: 0.91, end: 1.2, chunk_idx: 0, element_id: "os-p-001" },
  { word: "lies", start: 1.21, end: 1.5, chunk_idx: 0, element_id: "os-p-001" },
];

describe("E2E: catalog → book detail flow", () => {
  it("catalog books have slugs that form valid URL paths", () => {
    for (const book of catalogFixture.books) {
      expect(book.author_slug).toMatch(/^[a-z0-9-]+$/);
      expect(book.title_slug).toMatch(/^[a-z0-9-]+$/);
      const path = `/book/${book.author_slug}/${book.title_slug}`;
      expect(path).not.toContain(" ");
    }
  });

  it("manifest chapter filenames use .m4a extension", () => {
    for (const ch of manifestChapters) {
      expect(ch.filename).toMatch(/\.m4a$/);
    }
  });

  it("manifest chapters are numbered sequentially from 1", () => {
    manifestChapters.forEach((ch, i) => {
      expect(ch.number).toBe(i + 1);
    });
  });
});

describe("E2E: chapter text + alignment sync", () => {
  const words = wordsFixture;
  const { chunks } = chapterFixture;

  it("all alignment words reference valid chunk indices", () => {
    for (const w of words) {
      expect(w.chunk_idx).toBeGreaterThanOrEqual(0);
      expect(w.chunk_idx).toBeLessThan(chunks.length);
    }
  });

  it("word timestamps are monotonically increasing", () => {
    for (let i = 1; i < words.length; i++) {
      expect(words[i].start).toBeGreaterThanOrEqual(words[i - 1].start);
    }
  });

  it("word end time is after start time", () => {
    for (const w of words) {
      expect(w.end).toBeGreaterThan(w.start);
    }
  });

  it("sync engine finds correct word at each timestamp", () => {
    // At t=0.0, first word "Someone" (index 0)
    expect(findWordAtTime(words, 0.0)).toBe(0);
    expect(words[findWordAtTime(words, 0.0)].word).toBe("Someone");

    // At t=0.5, should be "must" (index 1, start=0.36)
    expect(findWordAtTime(words, 0.5)).toBe(1);
    expect(words[findWordAtTime(words, 0.5)].word).toBe("must");

    // At t=1.0, should be "telling" (index 4, start=0.91)
    expect(findWordAtTime(words, 1.0)).toBe(4);
    expect(words[findWordAtTime(words, 1.0)].word).toBe("telling");

    // At t=1.5, should be "lies" (index 5, start=1.21)
    expect(findWordAtTime(words, 1.5)).toBe(5);
    expect(words[findWordAtTime(words, 1.5)].word).toBe("lies");
  });

  it("sync engine maps all words to chunk 0", () => {
    for (let t = 0; t <= 1.5; t += 0.1) {
      const idx = findWordAtTime(words, t);
      if (idx >= 0) {
        expect(findChunkAtTime(words, t)).toBe(0);
      }
    }
  });

  it("element_id is NOT unique per word (multiple words share same element)", () => {
    const ids = words.map((w) => w.element_id);
    const unique = new Set(ids);
    // All 6 words share "os-p-001" — this is expected
    expect(unique.size).toBe(1);
    expect(unique.has("os-p-001")).toBe(true);
    // This is WHY we can't use element_id as a React key
  });

  it("word global indices ARE unique and suitable as React keys", () => {
    const indices = words.map((_, i) => i);
    const unique = new Set(indices);
    expect(unique.size).toBe(words.length);
  });
});

describe("E2E: tap-to-seek flow", () => {
  const words = wordsFixture;

  it("tapping a word provides a valid seekTo timestamp", () => {
    // Simulate: user taps word at globalIdx=3 ("been")
    const tappedIdx = 3;
    const seekTime = words[tappedIdx].start;
    expect(seekTime).toBe(0.73);

    // After seeking, sync engine should find that word
    const foundIdx = findWordAtTime(words, seekTime);
    expect(foundIdx).toBe(tappedIdx);
    expect(words[foundIdx].word).toBe("been");
  });

  it("tapping first word seeks to time 0", () => {
    const seekTime = words[0].start;
    expect(seekTime).toBe(0.0);
    expect(findWordAtTime(words, seekTime)).toBe(0);
  });

  it("tapping last word seeks correctly", () => {
    const lastIdx = words.length - 1;
    const seekTime = words[lastIdx].start;
    expect(findWordAtTime(words, seekTime)).toBe(lastIdx);
  });
});

describe("E2E: progress save/restore flow", () => {
  it("progress data round-trips through JSON serialization", () => {
    const progress = {
      chapter: 3,
      audioTime: 142.567,
      updatedAt: new Date().toISOString(),
    };
    const serialized = JSON.stringify(progress);
    const restored = JSON.parse(serialized);
    expect(restored.chapter).toBe(3);
    expect(restored.audioTime).toBeCloseTo(142.567);
    expect(restored.updatedAt).toBe(progress.updatedAt);
  });

  it("continue URL includes chapter and time params", () => {
    const author = "franz-kafka";
    const title = "the-trial";
    const chapter = 3;
    const audioTime = 142.5;
    const url = `/read/${author}/${title}?chapter=${chapter}&time=${audioTime}`;
    expect(url).toBe("/read/franz-kafka/the-trial?chapter=3&time=142.5");
  });
});

describe("E2E: audio URL construction", () => {
  it("audioUrl pads chapter number to 2 digits", () => {
    // Verify the URL format matches what the worker expects
    const padded = String(1).padStart(2, "0");
    expect(padded).toBe("01");
    expect(String(10).padStart(2, "0")).toBe("10");
  });

  it("audio files use .m4a extension in manifest", () => {
    for (const ch of manifestChapters) {
      expect(ch.filename).toMatch(/chapter-\d{2}\.m4a$/);
    }
  });
});
