import { Search, X } from "lucide-react-native";
import { useState } from "react";
import { Platform, Pressable, TextInput, View } from "react-native";
import type { TextStyle } from "react-native";
import { discoveryColors } from "../constants/discovery";
import { useTheme } from "../hooks/useTheme";

interface SearchBarProps {
	value: string;
	onChangeText: (text: string) => void;
	placeholder?: string;
}

export default function SearchBar({
	value,
	onChangeText,
	placeholder = "Search by title or author",
}: SearchBarProps) {
	const { colors, theme } = useTheme();
	const palette = discoveryColors(theme, colors);
	const [focused, setFocused] = useState(false);

	return (
			<View
				style={{
					flexDirection: "row",
					alignItems: "center",
					backgroundColor: palette.card,
					borderColor: focused ? palette.primary : palette.border,
					borderWidth: 1,
					borderRadius: 13,
					paddingHorizontal: 18,
					minHeight: 56,
				}}
			>
				<Search
					size={22}
					color={palette.muted}
					strokeWidth={2}
					style={{ marginRight: 12 }}
				/>
				<TextInput
					accessibilityLabel="Search books"
						onFocus={() => setFocused(true)}
						onBlur={() => setFocused(false)}
					value={value}
					onChangeText={onChangeText}
					placeholder={placeholder}
					placeholderTextColor={palette.muted}
					autoCorrect={false}
					returnKeyType="search"
					style={[{
						flex: 1,
						color: palette.text,
						fontSize: 16,
						paddingVertical: 0,
						minHeight: 54,
					// The visible focus ring is on the containing view; RN's type omits CSS "none".
					}, Platform.OS === "web" ? ({ outlineStyle: "none" } as unknown as TextStyle) : undefined]}
				/>
				{value.length > 0 && (
					<Pressable
						accessibilityRole="button"
						accessibilityLabel="Clear search"
						onPress={() => onChangeText("")}
						hitSlop={8}
						style={{ minWidth: 40, minHeight: 44, alignItems: "center", justifyContent: "center" }}
					>
						<X size={19} color={palette.muted} />
					</Pressable>
				)}
			</View>
	);
}
