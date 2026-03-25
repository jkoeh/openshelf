import { memo, useCallback, useMemo } from "react";
import { Text, View } from "react-native";
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
}

/**
 * A single chunk (paragraph) that renders with word-level highlighting
 * when it's the active chunk, or as plain text otherwise.
 * Memoized at the paragraph level so only the active paragraph re-renders.
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
}: SyncedChunkProps) {
  const { colors } = useTheme();

  // Only expand to word-level spans for active chunk and its neighbors
  const chunkWords = useMemo(
    () => words.filter((w) => w.chunk_idx === chunkIndex),
    [words, chunkIndex],
  );

  const handleWordPress = useCallback(
    (wordIdx: number) => {
      onWordPress?.(wordIdx);
    },
    [onWordPress],
  );

  // If no words for this chunk or not near active chunk, render plain text
  if (chunkWords.length === 0 || !isActiveChunk) {
    return (
      <View
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
      style={{
        marginBottom: 16,
        borderRadius: 4,
        paddingVertical: 2,
        paddingHorizontal: 4,
        backgroundColor: colors.chunkTint,
      }}
    >
      <Text style={{ flexDirection: "row", flexWrap: "wrap" }}>
        {chunkWords.map((w) => {
          // Find this word's global index in the words array
          const globalIdx = words.indexOf(w);
          return (
            <WordSpan
              key={w.element_id}
              word={w.word}
              isActive={globalIdx === activeWordIndex}
              highlightColor={colors.highlight}
              textColor={colors.text}
              fontSize={fontSize}
              lineHeight={lineHeight}
              onPress={onWordPress ? () => handleWordPress(globalIdx) : undefined}
            />
          );
        })}
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
}

export default function SyncedText({
  chunks,
  words,
  activeWordIndex,
  activeChunkIndex,
  fontSize,
  onWordPress,
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
          activeWordIndex={activeWordIndex}
          isActiveChunk={idx === activeChunkIndex}
          fontSize={fontSize}
          lineHeight={lineHeight}
          onWordPress={onWordPress}
        />
      ))}
    </>
  );
}
