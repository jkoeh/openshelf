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
    <View style={{ paddingHorizontal: 16, paddingVertical: 12 }}>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.textSecondary}
        style={{
          backgroundColor: colors.card,
          color: colors.text,
          borderWidth: 1,
          borderColor: colors.border,
          borderRadius: 8,
          paddingHorizontal: 14,
          paddingVertical: 10,
          fontSize: 16,
        }}
      />
    </View>
  );
}
