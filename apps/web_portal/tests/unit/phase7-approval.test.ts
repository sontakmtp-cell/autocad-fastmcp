// @vitest-environment node

import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { buildConsentDecision } from "@/lib/phase7-approval";
import type { Consent, ExecutionIntent } from "@/lib/contracts";
import type { PortalSession } from "@/lib/session";

const digest = (value: string) => `sha256:${value.repeat(64)}`;
const nonce = "nonce-value-at-least-sixteen-characters";
const ownerKey = `user-${"b".repeat(64)}`;
const session: PortalSession = {
  subject: "owner-a",
  ownerKey,
  displayName: "Owner A",
  accessToken: "server-token",
  csrfToken: "csrf-token-at-least-thirty-two-characters",
  expiresAt: 4_102_444_800,
  authenticatedAt: 2_000,
};
const intent: ExecutionIntent = {
  schema_version: "cad.execution-intent/1",
  intent_id: "intent-a-0001",
  intent_version: 1,
  owner_subject: ownerKey,
  action: "program_commit",
  state: "awaiting_approval",
  state_version: 0,
  device_id: "device-a-0001",
  document_id: "drawing33-document",
  expected_document_revision: "revision-001",
  program_id: "program-a-0001",
  program_revision: 1,
  preview_id: "preview-a-0001",
  risk_class: "medium",
  required_assurance: "user_recent_auth",
  trusted_effect_summary: [{ kind: "create_entities", count: 2, summary: "Tạo hai đối tượng" }],
  intent_digest: digest("a"),
  created_at: "2026-07-27T08:00:00.000Z",
  expires_at: "2099-07-27T08:10:00.000Z",
  consent_id: "consent-a-0001",
  released_job_id: null,
};
const consent: Consent = {
  schema_version: "cad.consent/1",
  consent_id: "consent-a-0001",
  consent_version: 1,
  owner_subject: ownerKey,
  intent_id: "intent-a-0001",
  intent_version: 1,
  intent_digest: digest("a"),
  required_assurance: "user_recent_auth",
  state: "requested",
  state_version: 0,
  challenge_nonce: nonce,
  challenge_nonce_hash: `sha256:${createHash("sha256").update(nonce).digest("hex")}`,
  requested_at: "2026-07-27T08:00:00.000Z",
  expires_at: "2099-07-27T08:10:00.000Z",
  decided_at: null,
  decision_source: null,
  consumed_at: null,
};

describe("Phase 7 approval binding", () => {
  it("builds only the exact locked Gateway mutation body", () => {
    expect(buildConsentDecision(session, intent, consent, "approve", Date.now())).toEqual({
      intent_digest: intent.intent_digest,
      consent_version: 1,
      challenge_nonce: nonce,
      decision: "approve",
    });
  });

  it("rejects session ownership mismatch, expiry and replay", () => {
    expect(() => buildConsentDecision(
      { ...session, ownerKey: `user-${"c".repeat(64)}` },
      intent,
      consent,
      "approve",
    )).toThrow("SESSION_RECORD_MISMATCH");
    expect(() => buildConsentDecision(
      session,
      intent,
      { ...consent, expires_at: "2020-01-01T00:00:00.000Z" },
      "approve",
    )).toThrow("APPROVAL_EXPIRED");
    expect(() => buildConsentDecision(
      session,
      intent,
      { ...consent, state: "consumed" },
      "approve",
    )).toThrow("CONSENT_NOT_PENDING");
  });
});
