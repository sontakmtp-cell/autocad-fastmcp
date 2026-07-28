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
  it("reads the authenticated Gateway Phase 6 release status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      program_v0_enabled: true,
      managed_write_enabled: false,
      kill_switch_active: true,
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const status = await new GatewayClient(session).getPhase6ReleaseStatus();

    expect(status.kill_switch_active).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:4321/api/portal/v1/phase6/status");
    expect(init.headers).toMatchObject({ authorization: "Bearer owner-a-token" });
  });

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

  it("keeps recent_auth_required distinguishable without exposing other forbidden records", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ code: "recent_auth_required" }),
      { status: 403, headers: { "content-type": "application/json" } },
    )));
    await expect(new GatewayClient(session).getConsent("consent-a-0001"))
      .rejects.toMatchObject({ status: 403, code: "recent_auth_required" });
  });

  it("submits exactly digest, version, nonce and decision for approval", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "approved",
      consent_id: "consent-a-0001",
      consent_version: 2,
      intent_id: "intent-a-0001",
    }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const body = {
      intent_digest: `sha256:${"a".repeat(64)}`,
      consent_version: 1,
      challenge_nonce: "nonce-at-least-sixteen-characters",
      decision: "approve" as const,
    };

    await new GatewayClient(session).decideConsent("consent-a-0001", "approve", body);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:4321/api/portal/v1/consents/consent-a-0001/approve",
    );
    expect(JSON.parse(String(init.body))).toEqual(body);
    expect(String(init.body)).not.toMatch(/owner|risk|assurance|effect/i);
    expect(init.headers).toMatchObject({
      origin: "http://127.0.0.1:3210",
      "x-csrf-token": body.challenge_nonce,
    });
  });

  it("reads Phase 6 resources through owner-scoped server routes", async () => {
    const digest = `sha256:${"a".repeat(64)}`;
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
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
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await new GatewayClient(session).getProgram("program-a-0001", 1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:4321/api/portal/v1/programs/program-a-0001/revisions/1",
    );
    expect(init.headers).toMatchObject({ authorization: "Bearer owner-a-token" });
    expect(url).not.toContain("owner-a");
  });
});
