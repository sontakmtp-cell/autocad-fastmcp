import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { recentAuthState, requireRecentAuth, requireSafeMutation } from "@/lib/security";
import { createOAuthTransaction } from "@/lib/oauth";
import type { PortalSession } from "@/lib/session";

const session: PortalSession = {
  subject: "owner-a",
  ownerKey: `user-${"a".repeat(64)}`,
  displayName: "Owner A",
  accessToken: "server-only-token",
  csrfToken: "csrf-token-at-least-thirty-two-characters",
  expiresAt: 4102444800,
  authenticatedAt: 2_000,
};

function request(
  origin: string,
  csrf: string,
  contentType = "application/x-www-form-urlencoded",
) {
  return new NextRequest("http://127.0.0.1:3210/api/bff/devices/device-a-0001/revoke", {
    method: "POST",
    headers: {
      origin,
      "content-type": contentType,
    },
    body: new URLSearchParams({ csrf }),
  });
}

describe("Portal mutations", () => {
  it("accepts the exact origin and CSRF token", async () => {
    const form = await requireSafeMutation(
      request("http://127.0.0.1:3210", session.csrfToken),
      session,
    );
    expect(form.get("csrf")).toBe(session.csrfToken);
  });

  it("rejects cross-origin and invalid-CSRF requests", async () => {
    await expect(requireSafeMutation(
      request("http://evil.test", session.csrfToken),
      session,
    )).rejects.toThrow("ORIGIN_REJECTED");
    await expect(requireSafeMutation(
      request("http://127.0.0.1:3210", "wrong"),
      session,
    )).rejects.toThrow("CSRF_REJECTED");
    await expect(requireSafeMutation(
      request("http://127.0.0.1:3210", session.csrfToken, "application/json"),
      session,
    )).rejects.toThrow("CONTENT_TYPE_REJECTED");
  });
});

describe("recent authentication", () => {
  it("fails closed for missing and stale auth_time, and accepts a bounded fresh value", () => {
    expect(recentAuthState({ ...session, authenticatedAt: undefined }, 2_100)).toBe("missing");
    expect(recentAuthState({ ...session, authenticatedAt: 1_000 }, 2_100)).toBe("stale");
    expect(recentAuthState({ ...session, authenticatedAt: 2_000 }, 2_100)).toBe("valid");
    expect(() => requireRecentAuth({ ...session, authenticatedAt: undefined }))
      .toThrow("RECENT_AUTH_REQUIRED");
  });
});

describe("OAuth return paths", () => {
  it("rejects cross-origin backslash paths", () => {
    expect(createOAuthTransaction("/\\evil.example").returnTo).toBe("/devices");
    expect(createOAuthTransaction("//evil.example").returnTo).toBe("/devices");
  });

  it("keeps only the Portal device and pairing routes", () => {
    expect(createOAuthTransaction("/pair?request=ABCD2345").returnTo)
      .toBe("/pair?request=ABCD2345");
    expect(createOAuthTransaction("/devices/device-a-0001").returnTo)
      .toBe("/devices/device-a-0001");
  });
});
