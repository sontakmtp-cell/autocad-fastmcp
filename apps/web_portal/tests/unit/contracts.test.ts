import { describe, expect, it } from "vitest";
import { deviceSchema, pairingSchema, parseOpaqueId } from "@/lib/contracts";

describe("Portal contracts", () => {
  it("accepts a bounded runtime-aware device", () => {
    const device = deviceSchema.parse({
      id: "device-a-0001",
      name: "Máy văn phòng",
      is_default: true,
      connected: true,
      last_seen_at: "2026-07-25T12:00:00.000Z",
      runtime: {
        label: ".NET R25",
        role: "primary",
        health: "ready",
      },
    });

    expect(device.runtime?.role).toBe("primary");
  });

  it("rejects path injection in opaque IDs", () => {
    expect(() => parseOpaqueId("../device-b-0001")).toThrow();
    expect(() => parseOpaqueId("device/b")).toThrow();
  });

  it("accepts Gateway UTC timestamps with an explicit offset", () => {
    const pairing = pairingSchema.parse({
      id: "pairing-a-0001",
      device_name: "Device A",
      requested_at: "2026-07-26T12:00:00.123456+00:00",
      expires_at: "2026-07-26T12:10:00.123456+00:00",
      status: "pending",
    });

    expect(pairing.status).toBe("pending");
  });
});
