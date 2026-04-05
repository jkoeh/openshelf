import { memo, useCallback } from "react";
import { Text } from "react-native";

interface WordSpanProps {
  word: string;
  isActive: boolean;
  wordIndex: number;
  highlightColor: string;
  textColor: string;
  fontSize: number;
  lineHeight: number;
  onWordPress?: (wordIndex: number) => void;
}

function WordSpanInner({
  word,
  isActive,
  wordIndex,
  highlightColor,
  textColor,
  fontSize,
  lineHeight,
  onWordPress,
}: WordSpanProps) {
  const handlePress = useCallback(() => {
    onWordPress?.(wordIndex);
  }, [onWordPress, wordIndex]);

  return (
    <Text
      onPress={onWordPress ? handlePress : undefined}
      style={{
        color: textColor,
        fontSize,
        lineHeight,
        backgroundColor: isActive ? highlightColor : "transparent",
        borderRadius: isActive ? 2 : 0,
      }}
    >
      {word}{" "}
    </Text>
  );
}

const WordSpan = memo(WordSpanInner);
export default WordSpan;
