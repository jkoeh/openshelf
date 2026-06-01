import { Pause, Play, SkipBack, SkipForward, type LucideIcon } from "lucide-react-native";
import { Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
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

function IconButton({
	Icon,
	color,
	size,
	bordered = false,
}: {
	Icon: LucideIcon;
	color: string;
	size: number;
	bordered?: boolean;
}) {
	return (
		<View
			style={{
				width: bordered ? 54 : 42,
				height: bordered ? 54 : 34,
				borderRadius: bordered ? 27 : 17,
				borderWidth: bordered ? 2 : 0,
				borderColor: bordered ? color : "transparent",
				alignItems: "center",
				justifyContent: "center",
			}}
		>
			<Icon size={size} color={color} strokeWidth={2.4} />
		</View>
	);
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
	const insets = useSafeAreaInsets();
	const progress = duration > 0 ? currentTime / duration : 0;

	return (
		<View
			style={{
				backgroundColor: colors.playerBg,
				borderTopWidth: 0.5,
				borderTopColor: colors.separator,
				paddingBottom: Math.max(insets.bottom, 8),
			}}
		>
			{/* Progress bar */}
			<Pressable
				onPress={(e) => {
					if (duration <= 0) return;
					const x = (e.nativeEvent as { offsetX?: number }).offsetX ?? e.nativeEvent.locationX;
					const target = e.currentTarget as unknown as { offsetWidth?: number };
					const barWidth = target?.offsetWidth ?? 300;
					const ratio = Math.max(0, Math.min(1, x / barWidth));
					onSeek(ratio * duration);
				}}
				style={{
					height: 20,
					justifyContent: "center",
					paddingHorizontal: 16,
				}}
			>
				<View
					style={{
						height: 3,
						backgroundColor: colors.border,
						borderRadius: 1.5,
						overflow: "hidden",
					}}
				>
					<View
						style={{
							height: 3,
							backgroundColor: colors.primary,
							borderRadius: 1.5,
							width: `${progress * 100}%`,
						}}
					/>
				</View>
			</Pressable>

			{/* Time labels */}
			<View
				style={{
					flexDirection: "row",
					justifyContent: "space-between",
					paddingHorizontal: 16,
					marginBottom: 6,
				}}
			>
				<Text style={{ color: colors.textSecondary, fontSize: 11, fontVariant: ["tabular-nums"] }}>
					{formatTime(currentTime)}
				</Text>
				<Text style={{ color: colors.textSecondary, fontSize: 11, fontVariant: ["tabular-nums"] }}>
					{duration > 0 ? `-${formatTime(duration - currentTime)}` : "--:--"}
				</Text>
			</View>

			{/* Controls */}
			<View
				style={{
					flexDirection: "row",
					alignItems: "center",
					justifyContent: "center",
					gap: 28,
					paddingHorizontal: 16,
				}}
			>
				{/* Rate */}
				<Pressable
					onPress={onRateChange}
					hitSlop={8}
					style={{
						paddingHorizontal: 6,
						paddingVertical: 3,
						borderRadius: 6,
						borderWidth: 1,
						borderColor: colors.border,
						minWidth: 42,
						alignItems: "center",
					}}
				>
					<Text
						style={{
							color: colors.text,
							fontSize: 13,
							fontWeight: "600",
							fontVariant: ["tabular-nums"],
						}}
					>
						{playbackRate}x
					</Text>
				</Pressable>

				{/* Skip back */}
				<Pressable
					onPress={() => onSeek(Math.max(0, currentTime - 15))}
					hitSlop={8}
					style={{
						width: 42,
						height: 34,
						borderRadius: 17,
						alignItems: "center",
						justifyContent: "center",
					}}
				>
					<Text style={{ color: colors.text, fontSize: 13, fontWeight: "700" }}>-15</Text>
				</Pressable>

				{/* Previous chapter */}
				<Pressable
					onPress={onPrev}
					disabled={!canPrev}
					hitSlop={8}
					style={{
						width: 42,
						height: 34,
						borderRadius: 17,
						alignItems: "center",
						justifyContent: "center",
						opacity: canPrev ? 1 : 0.25,
					}}
				>
					<IconButton Icon={SkipBack} size={24} color={colors.text} />
				</Pressable>

				{/* Play/Pause — central, prominent */}
				<Pressable
					onPress={onPlayPause}
					disabled={!isLoaded}
					hitSlop={8}
					style={{ opacity: isLoaded ? 1 : 0.4 }}
				>
					<IconButton
						Icon={playing ? Pause : Play}
						size={playing ? 28 : 30}
						color={colors.text}
						bordered
					/>
				</Pressable>

				{/* Next chapter */}
				<Pressable
					onPress={onNext}
					disabled={!canNext}
					hitSlop={8}
					style={{
						width: 42,
						height: 34,
						borderRadius: 17,
						alignItems: "center",
						justifyContent: "center",
						opacity: canNext ? 1 : 0.25,
					}}
				>
					<IconButton Icon={SkipForward} size={24} color={colors.text} />
				</Pressable>

				{/* Skip forward */}
				<Pressable
					onPress={() => onSeek(Math.min(duration, currentTime + 15))}
					hitSlop={8}
					style={{
						width: 42,
						height: 34,
						borderRadius: 17,
						alignItems: "center",
						justifyContent: "center",
					}}
				>
					<Text style={{ color: colors.text, fontSize: 13, fontWeight: "700" }}>+15</Text>
				</Pressable>

				{/* Placeholder for symmetry with rate button */}
				<View style={{ minWidth: 42 }} />
			</View>
		</View>
	);
}
