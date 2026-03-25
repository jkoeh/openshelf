import { describe, expect, it } from "vitest";
import { findChunkAtTime, findWordAtTime } from "../../lib/sync-engine";
import type { WordEntry } from "../../types";

function word(start: number, end: number, chunk_idx: number): WordEntry {
  return { word: `w${start}`, start, end, chunk_idx, element_id: `e${start}` };
}

const words: WordEntry[] = [
  word(0.0, 0.3, 0),
  word(0.4, 0.7, 0),
  word(0.8, 1.1, 0),
  word(1.5, 1.8, 1),
  word(2.0, 2.3, 1),
  word(3.0, 3.5, 2),
  word(4.0, 4.2, 2),
  word(5.0, 5.5, 3),
];

describe("findWordAtTime", () => {
  it("returns -1 before any words start", () => {
    expect(findWordAtTime(words, -1)).toBe(-1);
  });

  it("returns first word at time 0", () => {
    expect(findWordAtTime(words, 0.0)).toBe(0);
  });

  it("returns correct word in the middle of a chunk", () => {
    expect(findWordAtTime(words, 0.5)).toBe(1);
  });

  it("returns word at exact start boundary", () => {
    expect(findWordAtTime(words, 2.0)).toBe(4);
  });

  it("returns previous word in gap between words", () => {
    expect(findWordAtTime(words, 1.2)).toBe(2);
  });

  it("returns last word at time beyond all words", () => {
    expect(findWordAtTime(words, 100)).toBe(7);
  });

  it("returns -1 for empty array", () => {
    expect(findWordAtTime([], 1.0)).toBe(-1);
  });

  it("handles single-word array", () => {
    const single = [word(1.0, 1.5, 0)];
    expect(findWordAtTime(single, 0.5)).toBe(-1);
    expect(findWordAtTime(single, 1.0)).toBe(0);
    expect(findWordAtTime(single, 2.0)).toBe(0);
  });
});

describe("findChunkAtTime", () => {
  it("returns -1 before any words", () => {
    expect(findChunkAtTime(words, -1)).toBe(-1);
  });

  it("returns chunk 0 for early time", () => {
    expect(findChunkAtTime(words, 0.5)).toBe(0);
  });

  it("returns chunk 1 for mid time", () => {
    expect(findChunkAtTime(words, 2.0)).toBe(1);
  });

  it("returns chunk 2 for later time", () => {
    expect(findChunkAtTime(words, 3.2)).toBe(2);
  });

  it("returns last chunk for time past end", () => {
    expect(findChunkAtTime(words, 100)).toBe(3);
  });
});
