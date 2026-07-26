import { afterEach, describe, expect, it, vi } from "vitest";
import { authorizationUrl, createOAuthTransaction } from "@/lib/oauth";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OAuth account selection", () => {
  it("forces Auth0 to ask for credentials instead of silently reusing an account", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          authorization_endpoint: "https://issuer.test/authorize",
          token_endpoint: "https://issuer.test/oauth/token",
          userinfo_endpoint: "https://issuer.test/userinfo",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const url = await authorizationUrl(createOAuthTransaction("/devices"));

    expect(url.searchParams.get("prompt")).toBe("login");
    expect(url.searchParams.get("client_id")).toBe("unit-test-client");
    expect(url.searchParams.get("returnTo")).toBeNull();
  });
});
