import { defineConfig } from "@playwright/test";

const sessionSecret = "playwright-session-secret-at-least-32-characters";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:3210",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node tests/e2e/mock-gateway.mjs",
      port: 4321,
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3210",
      port: 3210,
      reuseExistingServer: false,
      env: {
        PORTAL_PUBLIC_ORIGIN: "http://127.0.0.1:3210",
        PORTAL_GATEWAY_BASE_URL: "http://127.0.0.1:4321",
        PORTAL_SESSION_SECRET: sessionSecret,
        PORTAL_OIDC_ISSUER: "http://127.0.0.1:4321/oidc/",
        PORTAL_OIDC_CLIENT_ID: "playwright-client",
        PORTAL_OIDC_AUDIENCE: "https://gateway.invalid/",
        PORTAL_PHASE6_UI_ENABLED: "true",
        PORTAL_MANAGED_WRITE_UI_ENABLED: "true",
        PORTAL_MANAGED_WRITE_KILL_SWITCH: "false",
        PORTAL_PHASE7_UI_ENABLED: "true",
        PORTAL_RECENT_AUTH_APPROVAL_ENABLED: "true",
        PORTAL_RECENT_AUTH_MAX_AGE_SECONDS: "300",
      },
    },
  ],
});
