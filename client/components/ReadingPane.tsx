import { forwardRef } from "react";
import { ScrollView, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";

interface ReadingPaneProps {
  chapterTitle: string;
  chunks: string[];
  fontSize: number;
}

const ReadingPane = forwardRef<ScrollView, ReadingPaneProps>(
  ({ chapterTitle, chunks, fontSize }, ref) => {
    const { colors } = useTheme();
    const lineHeight = Math.round(fontSize * 1.7);

    return (
      <ScrollView
        ref={ref}
        contentContainerStyle={{
          paddingHorizontal: 20,
          paddingTop: 24,
          paddingBottom: 100,
          maxWidth: 680,
          width: "100%",
          alignSelf: "center",
        }}
      >
        <Text
          style={{
            color: colors.text,
            fontSize: fontSize + 4,
            fontWeight: "700",
            textAlign: "center",
            marginBottom: 24,
          }}
        >
          {chapterTitle}
        </Text>
        {chunks.map((chunk, idx) => (
          <View
            key={idx}
            style={{
              marginBottom: 16,
              borderRadius: 4,
              paddingVertical: 2,
              paddingHorizontal: 4,
            }}
          >
            <Text
              style={{
                color: colors.text,
                fontSize,
                lineHeight,
              }}
            >
              {chunk}
            </Text>
          </View>
        ))}
      </ScrollView>
    );
  },
);

ReadingPane.displayName = "ReadingPane";

export default ReadingPane;
