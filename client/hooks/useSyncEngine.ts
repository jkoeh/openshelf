import { useEffect, useRef, useState } from "react";
import type { AudioPlayer } from "expo-audio";
import { Platform } from "react-native";
import { fetchChapterAlignment } from "../lib/api";
import { findWordAtTime } from "../lib/sync-engine";
import type { WordEntry } from "../types";

interface SyncState {
  words: WordEntry[];
  activeWordIndex: number;
  activeChunkIndex: number;
  loading: boolean;
}

/**
 * Hook that fetches alignment data for the current chapter and computes
 * the active word/chunk indices by reading player.currentTime directly
 * in a requestAnimationFrame loop.
 *
 * Only triggers React re-renders when the active word actually changes,
 * not on every audio time tick.
 *
 * Also prefetches alignment for the next chapter.
 */
export function useSyncEngine(
  author: string | undefined,
  title: string | undefined,
  chapter: number,
  totalChapters: number,
  player: AudioPlayer | null,
  syncEnabled: boolean,
  preloadedWords?: WordEntry[],
): SyncState {
  const [words, setWords] = useState<WordEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeWordIndex, setActiveWordIndex] = useState(-1);
  const [activeChunkIndex, setActiveChunkIndex] = useState(-1);
  const cache = useRef<Map<number, WordEntry[]>>(new Map());

  // Use preloaded words (from chapter_data.json) or fetch alignment separately
  useEffect(() => {
    if (!author || !title || !syncEnabled) {
      setWords([]);
      return;
    }

    // Words came inline with the chapter response — use them directly
    if (preloadedWords && preloadedWords.length > 0) {
      setWords(preloadedWords);
      return;
    }

    const cached = cache.current.get(chapter);
    if (cached) {
      setWords(cached);
      return;
    }

    setLoading(true);
    fetchChapterAlignment(author, title, chapter)
      .then((data) => {
        cache.current.set(chapter, data.words);
        setWords(data.words);
      })
      .catch(() => {
        setWords([]);
      })
      .finally(() => setLoading(false));
  }, [author, title, chapter, syncEnabled, preloadedWords]);

  // Prefetch next chapter's alignment (only when words aren't inline)
  useEffect(() => {
    if (!author || !title || !syncEnabled) return;
    if (preloadedWords && preloadedWords.length > 0) return;
    const next = chapter + 1;
    if (next > totalChapters || cache.current.has(next)) return;

    fetchChapterAlignment(author, title, next)
      .then((data) => {
        cache.current.set(next, data.words);
      })
      .catch(() => {
        // prefetch failure is non-critical
      });
  }, [author, title, chapter, totalChapters, syncEnabled, preloadedWords]);

  // rAF loop: read player.currentTime directly, only setState when indices change
  useEffect(() => {
    if (!syncEnabled || words.length === 0 || !player) {
      setActiveWordIndex(-1);
      setActiveChunkIndex(-1);
      return;
    }

    let raf: number;
    const tick = () => {
      const t = player.currentTime;
      const newWord = findWordAtTime(words, t);
      const newChunk = newWord >= 0 ? words[newWord].chunk_idx : -1;
      setActiveWordIndex((prev) => (prev === newWord ? prev : newWord));
      setActiveChunkIndex((prev) => (prev === newChunk ? prev : newChunk));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [player, words, syncEnabled]);

  return { words, activeWordIndex, activeChunkIndex, loading };
}
