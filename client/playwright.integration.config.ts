import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testDir: "./tests/integration",
  workers: 1,
  timeout: 60_000,
  use: { ...base.use, baseURL: "http://127.0.0.1:19007", trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: "npx expo start --web --port 19007",
    url: "http://127.0.0.1:19007",
    reuseExistingServer: false,
    timeout: 120_000,
    env: { CI: "1", EXPO_OFFLINE: "1", EXPO_PUBLIC_API_BASE: "http://127.0.0.1:8788/api/v1", EXPO_PUBLIC_GOOGLE_CLIENT_ID: "openshelf-test.apps.googleusercontent.com" },
  },
});
