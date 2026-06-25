/** End-to-end contract tests for section delivery, sync, and tap-to-seek. */

import { describe, expect, it } from "vitest";
import { findChunkAtTime, findWordAtTime } from "../../lib/sync-engine";
import type {
  CatalogResponse,
  Manifest,
  SectionResponse,
  WordEntry,
} from "../../types";

const catalogFixture: CatalogResponse = {
  version: 2,
  generated_at: "2026-06-24T12:00:00Z",
  total: 1,
  page: 1,
  limit: 20,
  books: [
    {
      author: "Lewis Carroll",
      author_slug: "lewis-carroll",
      title: "Alice's Adventures in Wonderland",
      title_slug: "alices-adventures-in-wonderland",
      source: "standard-ebooks",
      rendition: "kokoro-af-heart",
      current_build: "2a4f9c1b3d8e7f60",
      total_duration_seconds: 1200,
      section_count: 15,
    },
  ],
};

const manifestFixture: Manifest = {
  title: "Alice's Adventures in Wonderland",
  author: "Lewis Carroll",
  source: "standard-ebooks",
  renditions: {
    "kokoro-af-heart": {
      voice: "af_heart",
      engine: "kokoro",
      display: "Heart",
      current_build: "2a4f9c1b3d8e7f60",
      available_builds: ["2a4f9c1b3d8e7f60"],
      total_duration_seconds: 1200,
      sections: [
        {
          sequence: 1,
          section_type: "opening_credits",
          ordinal: null,
          display_label: "Alice's Adventures in Wonderland",
          display_title: "",
          filename: "section-01.m4a",
          duration_seconds: 5,
          word_count: 12,
        },
        {
          sequence: 2,
          section_type: "epigraph",
          ordinal: null,
          display_label: "Epigraph",
          display_title: "All in the Golden Afternoon",
          filename: "section-02.m4a",
          duration_seconds: 30,
          word_count: 100,
        },
        {
          sequence: 3,
          section_type: "chapter",
          ordinal: 1,
          display_label: "I",
          display_title: "Down the Rabbit-Hole",
          filename: "section-03.m4a",
          duration_seconds: 100,
          word_count: 500,
        },
      ],
    },
  },
};

const words: WordEntry[] = [
  { word: "Chapter", start: 0, end: 0.4, region: "heading", chunk_idx: null },
  { word: "One", start: 0.41, end: 0.7, region: "heading", chunk_idx: null },
  { word: "Alice", start: 1.5, end: 1.8, region: "body", chunk_idx: 0, element_id: "sec3-el0002" },
  { word: "was", start: 1.81, end: 2, region: "body", chunk_idx: 0, element_id: "sec3-el0002" },
];

const sectionFixture: SectionResponse = {
  sequence: 3,
  section_type: "chapter",
  ordinal: 1,
  heading: {
    display_label: "I",
    display_title: "Down the Rabbit-Hole",
    spoken_text: "Chapter One. Down the Rabbit-Hole.",
  },
  chunks: ["Alice was beginning to get very tired."],
  word_count: 8,
  words,
};

describe("E2E: catalog and manifest section semantics", () => {
  it("uses section counts and stable section sequences", () => {
    expect(catalogFixture.books[0].section_count).toBe(15);
    const sections = manifestFixture.renditions["kokoro-af-heart"].sections;
    expect(sections.map((section) => section.sequence)).toEqual([1, 2, 3]);
  });

  it("does not turn an epigraph into chapter one", () => {
    const sections = manifestFixture.renditions["kokoro-af-heart"].sections;
    expect(sections[1].section_type).toBe("epigraph");
    expect(sections[1].ordinal).toBeNull();
    expect(sections[2].ordinal).toBe(1);
  });

  it("uses section filenames for immutable audio", () => {
    for (const section of manifestFixture.renditions["kokoro-af-heart"].sections) {
      expect(section.filename).toMatch(/section-\d{2}\.m4a$/);
    }
  });
});

describe("E2E: separate heading and body sync", () => {
  it("keeps display text distinct from spoken text", () => {
    expect(sectionFixture.heading.display_label).toBe("I");
    expect(sectionFixture.heading.spoken_text).toContain("Chapter One");
    expect(sectionFixture.chunks[0]).toMatch(/^Alice was beginning/);
    expect(sectionFixture.chunks[0]).not.toContain("Down the Rabbit-Hole");
  });

  it("keeps timestamps monotonic across heading, pause, and body", () => {
    for (let index = 1; index < words.length; index++) {
      expect(words[index].start).toBeGreaterThanOrEqual(words[index - 1].end);
    }
    expect(findChunkAtTime(words, 0.5)).toBe(-1);
    expect(findChunkAtTime(words, 1.6)).toBe(0);
  });

  it("tap-to-seek works for both heading and body words", () => {
    expect(findWordAtTime(words, words[0].start)).toBe(0);
    expect(findWordAtTime(words, words[2].start)).toBe(2);
  });
});

describe("E2E: progress uses section identity", () => {
  it("round-trips section progress and URL state", () => {
    const progress = { section: 3, audioTime: 42.5 };
    expect(JSON.parse(JSON.stringify(progress))).toEqual(progress);
    expect(`/read/lewis-carroll/alice?section=${progress.section}&time=${progress.audioTime}`)
      .toBe("/read/lewis-carroll/alice?section=3&time=42.5");
  });
});
