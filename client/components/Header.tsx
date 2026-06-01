import { useRouter } from "expo-router";
import { ChevronLeft } from "lucide-react-native";
import { Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useTheme } from "../hooks/useTheme";

interface HeaderProps {
	title?: string;
	showBack?: boolean;
	rightElement?: React.ReactNode;
	large?: boolean;
}

export default function Header({
	title = "OpenShelf",
	showBack = false,
	rightElement,
	large = false,
}: HeaderProps) {
	const { colors } = useTheme();
	const insets = useSafeAreaInsets();
	const router = useRouter();

	return (
		<View
			style={{
				paddingTop: insets.top,
				backgroundColor: colors.background,
				borderBottomWidth: 0.5,
				borderBottomColor: colors.separator,
			}}
		>
			<View
				style={{
					flexDirection: "row",
					alignItems: "center",
					justifyContent: "space-between",
					paddingHorizontal: 16,
					height: 44,
				}}
			>
				<View style={{ flexDirection: "row", alignItems: "center", flex: 1 }}>
					{showBack ? (
						<Pressable
							onPress={() => router.back()}
							hitSlop={8}
							style={{ flexDirection: "row", alignItems: "center", marginRight: 8 }}
						>
							<ChevronLeft size={22} color={colors.primary} strokeWidth={2.4} />
							<Text style={{ color: colors.primary, fontSize: 17 }}>Back</Text>
						</Pressable>
					) : null}
					{!large ? (
						<Text
							style={{
								color: colors.text,
								fontSize: 17,
								fontWeight: "600",
								flex: 1,
							}}
							numberOfLines={1}
						>
							{title}
						</Text>
					) : null}
				</View>
				{rightElement ?? null}
			</View>
			{large ? (
				<Text
					style={{
						color: colors.text,
						fontSize: 34,
						fontWeight: "700",
						letterSpacing: 0.4,
						paddingHorizontal: 16,
						paddingBottom: 8,
					}}
				>
					{title}
				</Text>
			) : null}
		</View>
	);
}
