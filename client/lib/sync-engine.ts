import type { WordEntry } from "../types";

/**
 * Binary search to find the word being spoken at a given time.
 * Words are sorted by `start` time (server guarantees monotonic order).
 * Returns the index of the last word whose `start <= time`, or -1 if none.
 */
export function findWordAtTime(words: WordEntry[], time: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    if (words[mid].start <= time) {
      result = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return result;
}

/**
 * Find the chunk index that contains the active word.
 * Returns -1 if no active word.
 */
export function findChunkAtTime(words: WordEntry[], time: number): number {
  const wordIdx = findWordAtTime(words, time);
  if (wordIdx === -1) return -1;
  return words[wordIdx].chunk_idx;
}
