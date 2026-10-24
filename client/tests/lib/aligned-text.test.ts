import { describe, expect, it } from "vitest";
import { alignedTextParts } from "../../lib/aligned-text";

describe("original aligned reader text", () => {
  it("preserves Chinese script, punctuation and missing timings without spaces", () => {
    const text = "天地，玄黃。Hello 世界！";
    const parts = alignedTextParts(text, [{ word: "天", index: 1 }, { word: "地", index: 2 },
      { word: "黃", index: 4 }, { word: "Hello", index: 5 }, { word: "世", index: 6 }]);
    expect(parts.map(part => part.text).join("")).toBe(text);
    expect(parts.filter(part => part.index !== undefined).map(part => part.index)).toEqual([1, 2, 4, 5, 6]);
    expect(parts.find(part => part.text === "，玄")).toBeDefined();
  });
  it("retains English whitespace and repeat-word indices", () => {
    const text = "Hello, hello.\n\nUnaligned tail.";
    const parts = alignedTextParts(text, [{ word: "Hello", index: 0 }, { word: "hello", index: 1 }]);
    expect(parts.map(part => part.text).join("")).toBe(text);
    expect(parts[2]).toEqual({ text: "hello", index: 1 });
  });
});
