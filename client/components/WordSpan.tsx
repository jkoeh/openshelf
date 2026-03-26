import { memo } from "react";
import { Text } from "react-native";

interface WordSpanProps {
  word: string;
  isActive: boolean;
  highlightColor: string;
  textColor: string;
  fontSize: number;
  lineHeight: number;
  onPress?: () => void;
}

function WordSpanInner({
  word,
  isActive,
  highlightColor,
  textColor,
  fontSize,
  lineHeight,
  onPress,
}: WordSpanProps) {
  return (
    <Text
      onPress={onPress}
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
