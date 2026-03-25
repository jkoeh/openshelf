import { useEffect, useMemo, useRef, useState } from "react";
import { fetchChapterAlignment } from "../lib/api";
import { findChunkAtTime, findWordAtTime } from "../lib/sync-engine";
import type { WordEntry } from "../types";

interface SyncState {
  words: WordEntry[];
  activeWordIndex: number;
  activeChunkIndex: number;
  loading: boolean;
}

/**
 * Hook that fetches alignment data for the current chapter and computes
 * the active word/chunk indices from the audio's currentTime.
 *
 * Also prefetches alignment for the next chapter.
 */
export function useSyncEngine(
  author: string | undefined,
  title: string | undefined,
  chapter: number,
  totalChapters: number,
  currentTime: number,
  syncEnabled: boolean,
): SyncState {
  const [words, setWords] = useState<WordEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const cache = useRef<Map<number, WordEntry[]>>(new Map());

  // Fetch alignment for current chapter
  useEffect(() => {
    if (!author || !title || !syncEnabled) {
      setWords([]);
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
  }, [author, title, chapter, syncEnabled]);

  // Prefetch next chapter's alignment
  useEffect(() => {
    if (!author || !title || !syncEnabled) return;
    const next = chapter + 1;
    if (next > totalChapters || cache.current.has(next)) return;

    fetchChapterAlignment(author, title, next)
      .then((data) => {
        cache.current.set(next, data.words);
      })
      .catch(() => {
        // prefetch failure is non-critical
      });
  }, [author, title, chapter, totalChapters, syncEnabled]);

  // Compute active indices via binary search
  const { activeWordIndex, activeChunkIndex } = useMemo(() => {
    if (!syncEnabled || words.length === 0) {
      return { activeWordIndex: -1, activeChunkIndex: -1 };
    }
    return {
      activeWordIndex: findWordAtTime(words, currentTime),
      activeChunkIndex: findChunkAtTime(words, currentTime),
    };
  }, [words, currentTime, syncEnabled]);

  return { words, activeWordIndex, activeChunkIndex, loading };
}
