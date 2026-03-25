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
        style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "center" }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{
            backgroundColor: colors.card,
            marginHorizontal: 24,
            borderRadius: 12,
            maxHeight: "70%",
            borderWidth: 1,
            borderColor: colors.border,
            overflow: "hidden",
          }}
        >
          <Text
            style={{
              color: colors.text,
              fontSize: 16,
              fontWeight: "700",
              padding: 16,
              borderBottomWidth: 1,
              borderBottomColor: colors.border,
            }}
          >
            Chapters
          </Text>
          <FlatList
            data={chapters}
            keyExtractor={(ch) => String(ch.number)}
            renderItem={({ item: ch }) => (
              <Pressable
                onPress={() => {
                  onSelect(ch.number);
                  onClose();
                }}
                style={{
                  flexDirection: "row",
                  justifyContent: "space-between",
                  alignItems: "center",
                  paddingVertical: 14,
                  paddingHorizontal: 16,
                  backgroundColor: ch.number === currentChapter ? colors.chunkTint : "transparent",
                  borderBottomWidth: 1,
                  borderBottomColor: colors.border,
                }}
              >
                <Text
                  style={{
                    color: ch.number === currentChapter ? colors.primary : colors.text,
                    fontSize: 15,
                    fontWeight: ch.number === currentChapter ? "600" : "400",
                    flex: 1,
                    marginRight: 12,
                  }}
                  numberOfLines={1}
                >
                  {ch.number}. {ch.title}
                </Text>
              </Pressable>
            )}
          />
        </Pressable>
      </Pressable>
    </Modal>
  );
}
