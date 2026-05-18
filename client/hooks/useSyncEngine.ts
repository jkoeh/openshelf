import { useEffect, useRef, useState } from "react";
import type { AudioPlayer } from "expo-audio";
import { findWordAtTime } from "../lib/sync-engine";
import type { WordEntry } from "../types";

interface SyncState {
  words: WordEntry[];
  activeWordIndex: number;
  activeChunkIndex: number;
  loading: boolean;
}

/**
 * Hook that consumes inline chapter words and computes the active word/chunk
 * indices by reading player.currentTime directly in a requestAnimationFrame loop.
 *
 * Only triggers React re-renders when the active word actually changes,
 * not on every audio time tick.
 */
export function useSyncEngine(
  author: string | undefined,
  title: string | undefined,
  chapter: number,
  player: AudioPlayer | null,
  syncEnabled: boolean,
  preloadedWords?: WordEntry[],
  playbackTime?: number,
): SyncState {
  const [words, setWords] = useState<WordEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeWordIndex, setActiveWordIndex] = useState(-1);
  const [activeChunkIndex, setActiveChunkIndex] = useState(-1);
  const lastChapter = useRef<number | null>(null);

  // Use inline words from chapter_data.json.
  useEffect(() => {
    if (!author || !title || !syncEnabled) {
      setWords([]);
      return;
    }

    if (lastChapter.current !== chapter) {
      lastChapter.current = chapter;
      setActiveWordIndex(-1);
      setActiveChunkIndex(-1);
    }

    if (preloadedWords && preloadedWords.length > 0) {
      setWords(preloadedWords);
      return;
    }

    setWords([]);
    setLoading(false);
  }, [author, title, chapter, syncEnabled, preloadedWords]);

  // rAF loop: read player.currentTime directly, only setState when indices change
  useEffect(() => {
    if (!syncEnabled || words.length === 0 || !player) {
      setActiveWordIndex(-1);
      setActiveChunkIndex(-1);
      return;
    }

    let raf: number;
    const tick = () => {
      const t = typeof playbackTime === "number" ? playbackTime : player.currentTime;
      const newWord = findWordAtTime(words, t);
      const newChunk = newWord >= 0 ? words[newWord].chunk_idx : -1;
      setActiveWordIndex((prev) => (prev === newWord ? prev : newWord));
      setActiveChunkIndex((prev) => (prev === newChunk ? prev : newChunk));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [player, words, syncEnabled, playbackTime]);

  return { words, activeWordIndex, activeChunkIndex, loading };
}
