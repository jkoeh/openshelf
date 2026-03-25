import { Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { formatTime } from "../lib/format";

interface AudioPlayerBarProps {
  playing: boolean;
  currentTime: number;
  duration: number;
  playbackRate: number;
  isLoaded: boolean;
  onPlayPause: () => void;
  onSeek: (seconds: number) => void;
  onPrev: () => void;
  onNext: () => void;
  onRateChange: () => void;
  canPrev: boolean;
  canNext: boolean;
}

const RATE_OPTIONS = [0.75, 1, 1.25, 1.5, 1.75, 2];

export function nextRate(current: number): number {
  const idx = RATE_OPTIONS.findIndex((r) => r >= current);
  if (idx === -1 || idx === RATE_OPTIONS.length - 1) return RATE_OPTIONS[0];
  return RATE_OPTIONS[idx + 1];
}

export default function AudioPlayerBar({
  playing,
  currentTime,
  duration,
  playbackRate,
  isLoaded,
  onPlayPause,
  onSeek,
  onPrev,
  onNext,
  onRateChange,
  canPrev,
  canNext,
}: AudioPlayerBarProps) {
  const { colors } = useTheme();
  const progress = duration > 0 ? currentTime / duration : 0;

  return (
    <View
      style={{
        borderTopWidth: 1,
        borderTopColor: colors.border,
        backgroundColor: colors.card,
        paddingHorizontal: 16,
        paddingTop: 8,
        paddingBottom: 12,
      }}
    >
      {/* Progress bar */}
      <Pressable
        onPress={(e) => {
          if (duration <= 0) return;
          const { locationX } = e.nativeEvent;
          // Estimate bar width from a typical layout (will be approximate)
          // On web, nativeEvent has offsetX instead
          const x = (e.nativeEvent as { offsetX?: number }).offsetX ?? locationX;
          const target = e.currentTarget as unknown as { offsetWidth?: number };
          const barWidth = target?.offsetWidth ?? 300;
          const ratio = Math.max(0, Math.min(1, x / barWidth));
          onSeek(ratio * duration);
        }}
        style={{
          height: 24,
          justifyContent: "center",
          marginBottom: 4,
        }}
      >
        <View
          style={{
            height: 4,
            backgroundColor: colors.border,
            borderRadius: 2,
            overflow: "hidden",
          }}
        >
          <View
            style={{
              height: 4,
              backgroundColor: colors.primary,
              borderRadius: 2,
              width: `${progress * 100}%`,
            }}
          />
        </View>
      </Pressable>

      {/* Time labels */}
      <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 8 }}>
        <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
          {formatTime(currentTime)}
        </Text>
        <Text style={{ color: colors.textSecondary, fontSize: 12 }}>
          {duration > 0 ? formatTime(duration) : "--:--"}
        </Text>
      </View>

      {/* Controls */}
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 20 }}>
        <Pressable
          onPress={onRateChange}
          style={{
            paddingHorizontal: 8,
            paddingVertical: 4,
            borderRadius: 4,
            backgroundColor: colors.background,
            borderWidth: 1,
            borderColor: colors.border,
          }}
        >
          <Text style={{ color: colors.text, fontSize: 13, fontWeight: "500" }}>
            {playbackRate}x
          </Text>
        </Pressable>

        <Pressable onPress={onPrev} disabled={!canPrev} style={{ opacity: canPrev ? 1 : 0.3 }}>
          <Text style={{ color: colors.text, fontSize: 20 }}>{"<<"}</Text>
        </Pressable>

        <Pressable
          onPress={onPlayPause}
          disabled={!isLoaded}
          style={{
            width: 48,
            height: 48,
            borderRadius: 24,
            backgroundColor: colors.primary,
            alignItems: "center",
            justifyContent: "center",
            opacity: isLoaded ? 1 : 0.5,
          }}
        >
          <Text style={{ color: "#FFFFFF", fontSize: 20 }}>{playing ? "||" : "\u25B6"}</Text>
        </Pressable>

        <Pressable onPress={onNext} disabled={!canNext} style={{ opacity: canNext ? 1 : 0.3 }}>
          <Text style={{ color: colors.text, fontSize: 20 }}>{">>"}</Text>
        </Pressable>

        <Pressable
          onPress={() => onSeek(Math.min(duration, currentTime + 10))}
          style={{ paddingHorizontal: 8, paddingVertical: 4 }}
        >
          <Text style={{ color: colors.textSecondary, fontSize: 13 }}>+10s</Text>
        </Pressable>
      </View>
    </View>
  );
}
