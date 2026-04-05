import { Ionicons } from "@expo/vector-icons";
import { Modal, Pressable, Text, View } from "react-native";
import type { ThemeName } from "../constants/colors";
import { useTheme } from "../hooks/useTheme";

interface SettingsPanelProps {
	visible: boolean;
	onClose: () => void;
	fontSize: number;
	onFontSizeChange: (size: number) => void;
}

const THEME_OPTIONS: { key: ThemeName; label: string; icon: string }[] = [
	{ key: "light", label: "Light", icon: "sunny-outline" },
	{ key: "sepia", label: "Sepia", icon: "book-outline" },
	{ key: "dark", label: "Dark", icon: "moon-outline" },
];

const MIN_FONT = 14;
const MAX_FONT = 28;

export default function SettingsPanel({
	visible,
	onClose,
	fontSize,
	onFontSizeChange,
}: SettingsPanelProps) {
	const { theme, colors, setTheme } = useTheme();

	return (
		<Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
			<Pressable
				onPress={onClose}
				style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.3)", justifyContent: "flex-start" }}
			>
				<Pressable
					onPress={(e) => e.stopPropagation()}
					style={{
						backgroundColor: colors.card,
						marginTop: 56,
						marginHorizontal: 20,
						borderRadius: 14,
						padding: 20,
						shadowColor: "#000",
						shadowOffset: { width: 0, height: 8 },
						shadowOpacity: 0.15,
						shadowRadius: 20,
						elevation: 8,
					}}
				>
					{/* Theme */}
					<Text
						style={{
							color: colors.textSecondary,
							fontSize: 13,
							fontWeight: "600",
							textTransform: "uppercase",
							letterSpacing: 0.5,
							marginBottom: 10,
						}}
					>
						Theme
					</Text>
					<View style={{ flexDirection: "row", gap: 8, marginBottom: 24 }}>
						{THEME_OPTIONS.map((opt) => {
							const active = theme === opt.key;
							return (
								<Pressable
									key={opt.key}
									onPress={() => setTheme(opt.key)}
									style={{
										flex: 1,
										paddingVertical: 10,
										borderRadius: 10,
										backgroundColor: active ? colors.primary : colors.surface,
										alignItems: "center",
										gap: 4,
									}}
								>
									<Ionicons
										name={opt.icon as keyof typeof Ionicons.glyphMap}
										size={18}
										color={active ? colors.primaryText : colors.text}
									/>
									<Text
										style={{
											color: active ? colors.primaryText : colors.text,
											fontSize: 13,
											fontWeight: "500",
										}}
									>
										{opt.label}
									</Text>
								</Pressable>
							);
						})}
					</View>

					{/* Font Size */}
					<Text
						style={{
							color: colors.textSecondary,
							fontSize: 13,
							fontWeight: "600",
							textTransform: "uppercase",
							letterSpacing: 0.5,
							marginBottom: 12,
						}}
					>
						Text Size
					</Text>
					<View
						style={{
							flexDirection: "row",
							alignItems: "center",
							justifyContent: "center",
							gap: 20,
						}}
					>
						<Pressable
							onPress={() => onFontSizeChange(Math.max(MIN_FONT, fontSize - 2))}
							style={{
								width: 44,
								height: 44,
								borderRadius: 22,
								backgroundColor: colors.surface,
								alignItems: "center",
								justifyContent: "center",
							}}
						>
							<Text style={{ color: colors.text, fontSize: 22, fontWeight: "300" }}>A</Text>
						</Pressable>
						<View
							style={{
								width: 100,
								height: 3,
								backgroundColor: colors.border,
								borderRadius: 1.5,
								overflow: "hidden",
							}}
						>
							<View
								style={{
									width: `${((fontSize - MIN_FONT) / (MAX_FONT - MIN_FONT)) * 100}%`,
									height: 3,
									backgroundColor: colors.primary,
									borderRadius: 1.5,
								}}
							/>
						</View>
						<Pressable
							onPress={() => onFontSizeChange(Math.min(MAX_FONT, fontSize + 2))}
							style={{
								width: 44,
								height: 44,
								borderRadius: 22,
								backgroundColor: colors.surface,
								alignItems: "center",
								justifyContent: "center",
							}}
						>
							<Text style={{ color: colors.text, fontSize: 28, fontWeight: "300" }}>A</Text>
						</Pressable>
					</View>
				</Pressable>
			</Pressable>
		</Modal>
	);
}
