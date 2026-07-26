import "@testing-library/jest-dom/vitest";

process.env.PORTAL_PUBLIC_ORIGIN = "http://127.0.0.1:3210";
process.env.PORTAL_GATEWAY_BASE_URL = "http://127.0.0.1:4321";
process.env.PORTAL_SESSION_SECRET = "unit-test-session-secret-at-least-32-characters";
process.env.PORTAL_OIDC_ISSUER = "https://issuer.test/";
process.env.PORTAL_OIDC_CLIENT_ID = "unit-test-client";
process.env.PORTAL_OIDC_AUDIENCE = "https://gateway.test/";
