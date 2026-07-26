import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { requireSafeMutation } from "@/lib/security";
import { createOAuthTransaction } from "@/lib/oauth";
import type { PortalSession } from "@/lib/session";

const session: PortalSession = {
  subject: "owner-a",
  displayName: "Owner A",
  accessToken: "server-only-token",
  csrfToken: "csrf-token-at-least-thirty-two-characters",
  expiresAt: 4102444800,
};

function request(origin: string, csrf: string) {
  return new NextRequest("http://127.0.0.1:3210/api/bff/devices/device-a-0001/revoke", {
    method: "POST",
    headers: {
      origin,
      "content-type": "application/x-www-form-urlencoded",
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
