import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  type LayoutChangeEvent,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  ScrollView,
  Text,
  View,
} from "react-native";
import { useTheme } from "../hooks/useTheme";
import type { WordEntry } from "../types";
import SyncedText from "./SyncedText";

interface ReadingPaneProps {
  chapterTitle: string;
  chunks: string[];
  fontSize: number;
  words?: WordEntry[];
  activeWordIndex?: number;
  activeChunkIndex?: number;
  onWordPress?: (wordIndex: number) => void;
}

const AUTO_SCROLL_RESUME_DELAY = 5000;

const ReadingPane = forwardRef<ScrollView, ReadingPaneProps>(
  (
    {
      chapterTitle,
      chunks,
      fontSize,
      words,
      activeWordIndex = -1,
      activeChunkIndex = -1,
      onWordPress,
    },
    ref,
  ) => {
    const { colors } = useTheme();
    const lineHeight = Math.round(fontSize * 1.7);
    const hasSyncData = words && words.length > 0;

    const innerScrollRef = useRef<ScrollView>(null);
    const chunkPositions = useRef<Map<number, number>>(new Map());
    const [userScrolling, setUserScrolling] = useState(false);
    const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const lastScrolledChunk = useRef(-1);

    // Expose scrollTo via forwarded ref
    useImperativeHandle(ref, () => innerScrollRef.current as ScrollView);

    // Track chunk Y positions via onLayout
    const handleChunkLayout = useCallback((index: number, event: LayoutChangeEvent) => {
      chunkPositions.current.set(index, event.nativeEvent.layout.y);
    }, []);

    // Detect user-initiated scrolling
    const handleScrollBeginDrag = useCallback(() => {
      setUserScrolling(true);
      if (resumeTimer.current) {
        clearTimeout(resumeTimer.current);
      }
    }, []);

    const handleScrollEndDrag = useCallback(() => {
      resumeTimer.current = setTimeout(() => {
        setUserScrolling(false);
      }, AUTO_SCROLL_RESUME_DELAY);
    }, []);

    // Auto-scroll to active chunk when it changes
    useEffect(() => {
      if (
        userScrolling ||
        activeChunkIndex < 0 ||
        activeChunkIndex === lastScrolledChunk.current
      ) {
        return;
      }

      const y = chunkPositions.current.get(activeChunkIndex);
      if (y !== undefined) {
        lastScrolledChunk.current = activeChunkIndex;
        innerScrollRef.current?.scrollTo({ y: Math.max(0, y - 100), animated: true });
      }
    }, [activeChunkIndex, userScrolling]);

    // Reset on chapter change (when chunks change)
    useEffect(() => {
      lastScrolledChunk.current = -1;
      chunkPositions.current.clear();
    }, [chunks]);

    // Cleanup timer
    useEffect(() => {
      return () => {
        if (resumeTimer.current) clearTimeout(resumeTimer.current);
      };
    }, []);

    return (
      <ScrollView
        ref={innerScrollRef}
        onScrollBeginDrag={handleScrollBeginDrag}
        onScrollEndDrag={handleScrollEndDrag}
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
        {hasSyncData ? (
          <SyncedText
            chunks={chunks}
            words={words}
            activeWordIndex={activeWordIndex}
            activeChunkIndex={activeChunkIndex}
            fontSize={fontSize}
            onWordPress={onWordPress}
            onChunkLayout={handleChunkLayout}
          />
        ) : (
          chunks.map((chunk, idx) => (
            <View
              key={idx}
              style={{
                marginBottom: 16,
                borderRadius: 4,
                paddingVertical: 2,
                paddingHorizontal: 4,
              }}
            >
              <Text style={{ color: colors.text, fontSize, lineHeight }}>{chunk}</Text>
            </View>
          ))
        )}
      </ScrollView>
    );
  },
);

ReadingPane.displayName = "ReadingPane";

export default ReadingPane;
