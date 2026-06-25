import { Volume2, XCircle } from "lucide-react-native";
import { FlatList, Modal, Pressable, Text, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { sectionDisplayTitle } from "../lib/format";
import type { ManifestSection } from "../types";

interface SectionDropdownProps {
	visible: boolean;
	onClose: () => void;
	sections: ManifestSection[];
	currentSection: number;
	onSelect: (sequence: number) => void;
}

export default function ChapterDropdown({
	visible,
	onClose,
	sections,
	currentSection,
	onSelect,
}: SectionDropdownProps) {
	const { colors } = useTheme();
	return (
		<Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
			<Pressable
				onPress={onClose}
				style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.3)", justifyContent: "center" }}
			>
				<Pressable
					onPress={(event) => event.stopPropagation()}
					style={{
						backgroundColor: colors.card,
						marginHorizontal: 20,
						borderRadius: 14,
						maxHeight: "70%",
						overflow: "hidden",
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
							Sections
						</Text>
						<Pressable onPress={onClose} hitSlop={8}>
							<XCircle size={24} color={colors.textSecondary} strokeWidth={2.2} />
						</Pressable>
					</View>
					<FlatList
						data={sections}
						keyExtractor={(section) => String(section.sequence)}
						initialScrollIndex={Math.max(0, currentSection - 1)}
						getItemLayout={(_, index) => ({ length: 48, offset: 48 * index, index })}
						renderItem={({ item: section }) => {
							const active = section.sequence === currentSection;
							return (
								<Pressable
									onPress={() => {
										onSelect(section.sequence);
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
										}}
										numberOfLines={1}
									>
										{sectionDisplayTitle(section)}
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
