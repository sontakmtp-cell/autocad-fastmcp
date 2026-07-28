// @vitest-environment node

import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { afterEach, describe, expect, it, vi } from "vitest";
import { authorizationUrl, createOAuthTransaction, exchangeCode } from "@/lib/oauth";

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
          jwks_uri: "https://issuer.test/.well-known/jwks.json",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const url = await authorizationUrl(createOAuthTransaction("/devices"));

    expect(url.searchParams.get("prompt")).toBe("login");
    expect(url.searchParams.get("client_id")).toBe("unit-test-client");
    expect(url.searchParams.get("nonce")).toBeTruthy();
    expect(url.searchParams.get("returnTo")).toBeNull();
  });

  it("uses max_age for reauthentication and preserves an approval return path", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      authorization_endpoint: "https://issuer.test/authorize",
      token_endpoint: "https://issuer.test/oauth/token",
      userinfo_endpoint: "https://issuer.test/userinfo",
      jwks_uri: "https://issuer.test/.well-known/jwks.json",
    }), { status: 200 }));
    const transaction = createOAuthTransaction("/consents/consent-a-0001", "recent_auth");
    const url = await authorizationUrl(transaction);
    expect(url.searchParams.get("prompt")).toBe("login");
    expect(url.searchParams.get("max_age")).toBe("300");
    expect(url.searchParams.get("max_age")).toBe("300");
    expect(transaction.returnTo).toBe("/consents/consent-a-0001");
  });

  it("accepts auth_time only from a signature, issuer, audience and nonce verified ID token", async () => {
    const { publicKey, privateKey } = await generateKeyPair("RS256");
    const publicJwk = await exportJWK(publicKey);
    const now = Math.floor(Date.now() / 1000);
    const transaction = createOAuthTransaction("/intents/intent-a-0001", "recent_auth");
    const idToken = await new SignJWT({
      sub: "owner-a",
      nonce: transaction.nonce,
      auth_time: now,
    })
      .setProtectedHeader({ alg: "RS256", kid: "unit-key" })
      .setIssuer("https://issuer.test")
      .setAudience("unit-test-client")
      .setIssuedAt(now)
      .setExpirationTime(now + 300)
      .sign(privateKey);
    publicJwk.kid = "unit-key";
    publicJwk.alg = "RS256";

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/.well-known/openid-configuration")) {
        return new Response(JSON.stringify({
          authorization_endpoint: "https://issuer.test/authorize",
          token_endpoint: "https://issuer.test/oauth/token",
          userinfo_endpoint: "https://issuer.test/userinfo",
          jwks_uri: "https://issuer.test/.well-known/jwks.json",
        }), { status: 200 });
      }
      if (url.endsWith("/oauth/token")) {
        return new Response(JSON.stringify({
          access_token: "access-token",
          id_token: idToken,
          expires_in: 3600,
        }), { status: 200 });
      }
      if (url.endsWith("/.well-known/jwks.json")) {
        return new Response(JSON.stringify({ keys: [publicJwk] }), { status: 200 });
      }
      return new Response(JSON.stringify({ sub: "owner-a", name: "Owner A" }), { status: 200 });
    });

    await expect(exchangeCode("code", transaction)).resolves.toMatchObject({
      subject: "owner-a",
      authenticatedAt: now,
    });
  });
});
