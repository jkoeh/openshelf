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

function Triangle({
	direction,
	color,
	size,
}: {
	direction: "left" | "right";
	color: string;
	size: number;
}) {
	return (
		<View
			style={{
				width: 0,
				height: 0,
				borderTopWidth: size * 0.55,
				borderBottomWidth: size * 0.55,
				borderTopColor: "transparent",
				borderBottomColor: "transparent",
				...(direction === "right"
					? { borderLeftWidth: size, borderLeftColor: color }
					: { borderRightWidth: size, borderRightColor: color }),
			}}
		/>
	);
}

function ChapterIcon({ direction, color }: { direction: "prev" | "next"; color: string }) {
	const forward = direction === "next";

	return (
		<View
			style={{
				width: 34,
				height: 34,
				flexDirection: "row",
				alignItems: "center",
				justifyContent: "center",
			}}
		>
			{!forward && (
				<View
					style={{
						width: 3,
						height: 18,
						borderRadius: 1.5,
						backgroundColor: color,
						marginRight: 3,
					}}
				/>
			)}
			<Triangle direction={forward ? "right" : "left"} color={color} size={13} />
			{forward && (
				<View
					style={{
						width: 3,
						height: 18,
						borderRadius: 1.5,
						backgroundColor: color,
						marginLeft: 3,
					}}
				/>
			)}
		</View>
	);
}

function PlayPauseIcon({ playing, color }: { playing: boolean; color: string }) {
	return (
		<View
			style={{
				width: 54,
				height: 54,
				borderRadius: 27,
				borderWidth: 2,
				borderColor: color,
				alignItems: "center",
				justifyContent: "center",
			}}
		>
			{playing ? (
				<View style={{ flexDirection: "row", gap: 6 }}>
					<View style={{ width: 6, height: 24, borderRadius: 2, backgroundColor: color }} />
					<View style={{ width: 6, height: 24, borderRadius: 2, backgroundColor: color }} />
				</View>
			) : (
				<View style={{ marginLeft: 4 }}>
					<Triangle direction="right" color={color} size={19} />
				</View>
			)}
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
					<ChapterIcon direction="prev" color={colors.text} />
				</Pressable>

				{/* Play/Pause — central, prominent */}
				<Pressable
					onPress={onPlayPause}
					disabled={!isLoaded}
					hitSlop={8}
					style={{ opacity: isLoaded ? 1 : 0.4 }}
				>
					<PlayPauseIcon playing={playing} color={colors.text} />
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
					<ChapterIcon direction="next" color={colors.text} />
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
