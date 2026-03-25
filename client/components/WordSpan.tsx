import { memo } from "react";
import { Pressable, Text } from "react-native";

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
  if (onPress) {
    return (
      <Pressable onPress={onPress}>
        <Text
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
      </Pressable>
    );
  }

  return (
    <Text
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
