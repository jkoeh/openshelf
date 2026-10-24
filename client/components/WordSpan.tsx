import { memo, useCallback } from "react";
import { Text } from "react-native";

interface WordSpanProps {
  word: string;
  trailingSpace?: boolean;
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
  trailingSpace = true,
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
      {word}{trailingSpace ? " " : ""}
    </Text>
  );
}

const WordSpan = memo(WordSpanInner);
export default WordSpan;
