import { Modal, Pressable, Text, View } from "react-native";
import type { ThemeName } from "../constants/colors";
import { useTheme } from "../hooks/useTheme";

interface SettingsPanelProps {
  visible: boolean;
  onClose: () => void;
  fontSize: number;
  onFontSizeChange: (size: number) => void;
}

const THEME_OPTIONS: { key: ThemeName; label: string }[] = [
  { key: "light", label: "Light" },
  { key: "sepia", label: "Sepia" },
  { key: "dark", label: "Dark" },
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
        style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-start" }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{
            backgroundColor: colors.card,
            marginTop: 60,
            marginHorizontal: 24,
            borderRadius: 12,
            padding: 20,
            borderWidth: 1,
            borderColor: colors.border,
          }}
        >
          {/* Theme */}
          <Text style={{ color: colors.text, fontSize: 14, fontWeight: "600", marginBottom: 10 }}>
            Theme
          </Text>
          <View style={{ flexDirection: "row", gap: 8, marginBottom: 20 }}>
            {THEME_OPTIONS.map((opt) => (
              <Pressable
                key={opt.key}
                onPress={() => setTheme(opt.key)}
                style={{
                  flex: 1,
                  paddingVertical: 10,
                  borderRadius: 8,
                  borderWidth: 1,
                  borderColor: theme === opt.key ? colors.primary : colors.border,
                  backgroundColor: theme === opt.key ? colors.primary : colors.background,
                  alignItems: "center",
                }}
              >
                <Text
                  style={{
                    color: theme === opt.key ? "#FFFFFF" : colors.text,
                    fontSize: 14,
                    fontWeight: "500",
                  }}
                >
                  {opt.label}
                </Text>
              </Pressable>
            ))}
          </View>

          {/* Font Size */}
          <Text style={{ color: colors.text, fontSize: 14, fontWeight: "600", marginBottom: 10 }}>
            Font Size
          </Text>
          <View
            style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 16 }}
          >
            <Pressable
              onPress={() => onFontSizeChange(Math.max(MIN_FONT, fontSize - 2))}
              style={{
                width: 40,
                height: 40,
                borderRadius: 20,
                backgroundColor: colors.background,
                borderWidth: 1,
                borderColor: colors.border,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Text style={{ color: colors.text, fontSize: 20 }}>-</Text>
            </Pressable>
            <Text style={{ color: colors.text, fontSize: 16, minWidth: 50, textAlign: "center" }}>
              {fontSize}px
            </Text>
            <Pressable
              onPress={() => onFontSizeChange(Math.min(MAX_FONT, fontSize + 2))}
              style={{
                width: 40,
                height: 40,
                borderRadius: 20,
                backgroundColor: colors.background,
                borderWidth: 1,
                borderColor: colors.border,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Text style={{ color: colors.text, fontSize: 20 }}>+</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
