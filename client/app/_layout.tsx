import "../global.css";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ThemeProvider, useTheme } from "../hooks/useTheme";
import { OwnerProvider } from "../hooks/useOwner";

function ThemedStack() {
  const { colors, theme } = useTheme();

  return (
    <>
      <StatusBar style={theme === "dark" ? "light" : "dark"} />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.background },
        }}
      />
    </>
  );
}

export default function RootLayout() {
  return (
    <ThemeProvider>
      <OwnerProvider><ThemedStack /></OwnerProvider>
    </ThemeProvider>
  );
}
