import { afterEach, describe, expect, it, vi } from "vitest";
import { GatewayClient } from "@/lib/gateway-client";
import type { PortalSession } from "@/lib/session";

const session: PortalSession = {
  subject: "owner-a",
  displayName: "Owner A",
  accessToken: "owner-a-token",
  csrfToken: "csrf-token-at-least-thirty-two-characters",
  expiresAt: 4102444800,
};

afterEach(() => vi.unstubAllGlobals());

describe("GatewayClient owner boundary", () => {
  it("uses only the server-side bearer token and never sends an owner field", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ devices: [] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await new GatewayClient(session).listDevices();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:4321/api/portal/v1/devices");
    expect(url).not.toContain("owner");
    expect(init.headers).toMatchObject({ authorization: "Bearer owner-a-token" });
    expect(init.body).toBeUndefined();
  });

  it("normalizes cross-owner forbidden responses to not found", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 403 })));
    await expect(new GatewayClient(session).getDevice("device-b-0001"))
      .rejects.toMatchObject({ status: 404 });
  });
});
