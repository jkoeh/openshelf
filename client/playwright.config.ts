import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "**/*.pw.ts",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:19006",
    browserName: "chromium",
    channel: process.env.CI ? undefined : "msedge",
    headless: true,
  },
  webServer: {
    command: "npx expo start --web --port 19006",
    url: "http://127.0.0.1:19006",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { EXPO_PUBLIC_API_BASE: "http://127.0.0.1:8787/api/v1", CI: "1" },
  },
});
