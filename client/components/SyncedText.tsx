import { memo, useCallback, useMemo } from "react";
import { type LayoutChangeEvent, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import type { WordEntry } from "../types";
import WordSpan from "./WordSpan";

interface SyncedChunkProps {
  chunkIndex: number;
  chunkText: string;
  words: WordEntry[];
  activeWordIndex: number;
  isActiveChunk: boolean;
  fontSize: number;
  lineHeight: number;
  onWordPress?: (wordIndex: number) => void;
  onChunkLayout?: (index: number, event: LayoutChangeEvent) => void;
}

/**
 * A single chunk (paragraph) that renders with word-level highlighting
 * when it's the active chunk, or as plain text otherwise.
 * Memoized at the paragraph level so only the active paragraph re-renders.
 *
 * Key memo invariant: non-active chunks receive activeWordIndex=-1,
 * so their props don't change on every audio tick.
 */
const SyncedChunk = memo(function SyncedChunk({
  chunkIndex,
  chunkText,
  words,
  activeWordIndex,
  isActiveChunk,
  fontSize,
  lineHeight,
  onWordPress,
  onChunkLayout,
}: SyncedChunkProps) {
  const { colors } = useTheme();

  const handleLayout = useCallback(
    (e: LayoutChangeEvent) => onChunkLayout?.(chunkIndex, e),
    [onChunkLayout, chunkIndex],
  );

  // Collect words for this chunk with their global indices
  const chunkWords = useMemo(() => {
    const result: { word: WordEntry; globalIdx: number }[] = [];
    for (let i = 0; i < words.length; i++) {
      if (words[i].chunk_idx === chunkIndex) {
        result.push({ word: words[i], globalIdx: i });
      }
    }
    return result;
  }, [words, chunkIndex]);

  // If no words for this chunk or not the active chunk, render plain text
  if (chunkWords.length === 0 || !isActiveChunk) {
    return (
      <View
        onLayout={handleLayout}
        style={{
          marginBottom: 16,
          borderRadius: 4,
          paddingVertical: 2,
          paddingHorizontal: 4,
        }}
      >
        <Text style={{ color: colors.text, fontSize, lineHeight }}>{chunkText}</Text>
      </View>
    );
  }

  // Render word-level spans for active chunk
  return (
    <View
      onLayout={handleLayout}
      style={{
        marginBottom: 16,
        borderRadius: 4,
        paddingVertical: 2,
        paddingHorizontal: 4,
        backgroundColor: colors.chunkTint,
      }}
    >
      <Text style={{ flexDirection: "row", flexWrap: "wrap" }}>
        {chunkWords.map(({ word: w, globalIdx }) => (
          <WordSpan
            key={globalIdx}
            word={w.word}
            isActive={globalIdx === activeWordIndex}
            wordIndex={globalIdx}
            highlightColor={colors.highlight}
            textColor={colors.text}
            fontSize={fontSize}
            lineHeight={lineHeight}
            onWordPress={onWordPress}
          />
        ))}
      </Text>
    </View>
  );
});

interface SyncedTextProps {
  chunks: string[];
  words: WordEntry[];
  activeWordIndex: number;
  activeChunkIndex: number;
  fontSize: number;
  onWordPress?: (wordIndex: number) => void;
  onChunkLayout?: (index: number, event: LayoutChangeEvent) => void;
}

export default function SyncedText({
  chunks,
  words,
  activeWordIndex,
  activeChunkIndex,
  fontSize,
  onWordPress,
  onChunkLayout,
}: SyncedTextProps) {
  const lineHeight = Math.round(fontSize * 1.7);

  return (
    <>
      {chunks.map((chunk, idx) => (
        <SyncedChunk
          key={idx}
          chunkIndex={idx}
          chunkText={chunk}
          words={words}
          activeWordIndex={idx === activeChunkIndex ? activeWordIndex : -1}
          isActiveChunk={idx === activeChunkIndex}
          fontSize={fontSize}
          lineHeight={lineHeight}
          onWordPress={onWordPress}
          onChunkLayout={onChunkLayout}
        />
      ))}
    </>
  );
}
