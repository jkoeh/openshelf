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
  onLayout?: (event: LayoutChangeEvent) => void;
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
  onLayout,
}: SyncedChunkProps) {
  const { colors } = useTheme();

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
        onLayout={onLayout}
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
      onLayout={onLayout}
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
            highlightColor={colors.highlight}
            textColor={colors.text}
            fontSize={fontSize}
            lineHeight={lineHeight}
            onPress={onWordPress ? () => handleWordPress(globalIdx) : undefined}
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
          activeWordIndex={activeWordIndex}
          isActiveChunk={idx === activeChunkIndex}
          fontSize={fontSize}
          lineHeight={lineHeight}
          onWordPress={onWordPress}
          onLayout={onChunkLayout ? (e) => onChunkLayout(idx, e) : undefined}
        />
      ))}
    </>
  );
}
