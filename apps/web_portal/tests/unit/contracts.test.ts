import { describe, expect, it } from "vitest";
import {
  deviceSchema,
  pairingSchema,
  parseOpaqueId,
  previewSchema,
  programRevisionSchema,
} from "@/lib/contracts";

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

describe("Phase 6 resource contracts", () => {
  const digest = `sha256:${"a".repeat(64)}`;

  it("accepts a strict cad.program/0.2 owner-safe summary", () => {
    const value = programRevisionSchema.parse({
      program_id: "program-a-0001",
      program_revision: 1,
      device_id: "device-a-0001",
      document_id: "drawing33-document",
      source_snapshot_id: "snapshot-a-0001",
      expected_document_revision: "revision-001",
      schema_version: "cad.program/0.2",
      program_digest: digest,
      risk_class: "low",
      missing_capabilities: [],
      pins: {
        runtime_id: "managed_dotnet_r25",
        runtime_role: "primary",
        host_family: "R25",
        host_version: "25.0",
        package_id: "managed-host",
        package_version: "0.2.0",
        package_hash: digest,
        capability_manifest_hash: digest,
        operation_registry_hash: digest,
        registry_version: "registry/0.2",
        policy_version: "phase6-policy/1",
      },
      created_at: "2026-07-27T08:00:00.000Z",
      owner_subject: "must-not-reach-browser-components",
    });
    expect(value).not.toHaveProperty("owner_subject");
  });

  it("rejects an unpinned preview digest", () => {
    expect(() => previewSchema.parse({
      preview_id: "preview-a-0001",
      package_hash: "not-a-digest",
    })).toThrow();
  });
});
