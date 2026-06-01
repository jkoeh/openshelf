import { Volume2, XCircle } from "lucide-react-native";
import { FlatList, Modal, Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import type { ManifestChapter } from "../types";

interface ChapterDropdownProps {
	visible: boolean;
	onClose: () => void;
	chapters: ManifestChapter[];
	currentChapter: number;
	onSelect: (chapter: number) => void;
}

export default function ChapterDropdown({
	visible,
	onClose,
	chapters,
	currentChapter,
	onSelect,
}: ChapterDropdownProps) {
	const { colors } = useTheme();

	return (
		<Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
			<Pressable
				onPress={onClose}
				style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.3)", justifyContent: "center" }}
			>
				<Pressable
					onPress={(e) => e.stopPropagation()}
					style={{
						backgroundColor: colors.card,
						marginHorizontal: 20,
						borderRadius: 14,
						maxHeight: "70%",
						overflow: "hidden",
						shadowColor: "#000",
						shadowOffset: { width: 0, height: 8 },
						shadowOpacity: 0.15,
						shadowRadius: 20,
						elevation: 8,
					}}
				>
					<View
						style={{
							flexDirection: "row",
							justifyContent: "space-between",
							alignItems: "center",
							padding: 16,
							borderBottomWidth: 0.5,
							borderBottomColor: colors.separator,
						}}
					>
						<Text style={{ color: colors.text, fontSize: 17, fontWeight: "600" }}>
							Chapters
						</Text>
						<Pressable onPress={onClose} hitSlop={8}>
							<XCircle size={24} color={colors.textSecondary} strokeWidth={2.2} />
						</Pressable>
					</View>
					<FlatList
						data={chapters}
						keyExtractor={(ch) => String(ch.number)}
						initialScrollIndex={Math.max(0, currentChapter - 1)}
						getItemLayout={(_, index) => ({
							length: 48,
							offset: 48 * index,
							index,
						})}
						renderItem={({ item: ch }) => {
							const active = ch.number === currentChapter;
							return (
								<Pressable
									onPress={() => {
										onSelect(ch.number);
										onClose();
									}}
									style={({ pressed }) => ({
										flexDirection: "row",
										alignItems: "center",
										paddingVertical: 13,
										paddingHorizontal: 16,
										height: 48,
										backgroundColor: pressed ? colors.surface : "transparent",
									})}
								>
									{active ? (
										<Volume2
											size={16}
											color={colors.primary}
											strokeWidth={2.4}
											style={{ marginRight: 10, width: 20 }}
										/>
									) : (
										<View style={{ width: 20, marginRight: 10 }} />
									)}
									<Text
										style={{
											color: active ? colors.primary : colors.text,
											fontSize: 17,
											fontWeight: active ? "600" : "400",
											flex: 1,
											letterSpacing: -0.2,
										}}
										numberOfLines={1}
									>
										{ch.number}. {ch.title}
									</Text>
								</Pressable>
							);
						}}
					/>
				</Pressable>
			</Pressable>
		</Modal>
	);
}
