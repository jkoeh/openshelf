import { Ionicons } from "@expo/vector-icons";
import { TextInput, View } from "react-native";
import { useTheme } from "../hooks/useTheme";

interface SearchBarProps {
	value: string;
	onChangeText: (text: string) => void;
	placeholder?: string;
}

export default function SearchBar({
	value,
	onChangeText,
	placeholder = "Search books...",
}: SearchBarProps) {
	const { colors } = useTheme();

	return (
		<View style={{ paddingHorizontal: 16, paddingVertical: 10 }}>
			<View
				style={{
					flexDirection: "row",
					alignItems: "center",
					backgroundColor: colors.surface,
					borderRadius: 10,
					paddingHorizontal: 10,
					height: 36,
				}}
			>
				<Ionicons
					name="search"
					size={17}
					color={colors.textSecondary}
					style={{ marginRight: 6 }}
				/>
				<TextInput
					value={value}
					onChangeText={onChangeText}
					placeholder={placeholder}
					placeholderTextColor={colors.textSecondary}
					style={{
						flex: 1,
						color: colors.text,
						fontSize: 17,
						paddingVertical: 0,
					}}
				/>
			</View>
		</View>
	);
}
