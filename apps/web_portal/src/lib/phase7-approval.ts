import "server-only";
import { createHash } from "node:crypto";
import type { Consent, ExecutionIntent } from "./contracts";
import type { PortalSession } from "./session";

export type ConsentDecision = "approve" | "deny";

export function buildConsentDecision(
  session: PortalSession,
  intent: ExecutionIntent,
  consent: Consent,
  decision: ConsentDecision,
  now = Date.now(),
) {
  if (
    !session.ownerKey
    || intent.owner_subject !== session.ownerKey
    || consent.owner_subject !== session.ownerKey
    || consent.intent_id !== intent.intent_id
    || consent.intent_version !== intent.intent_version
    || consent.intent_digest !== intent.intent_digest
  ) {
    throw new Error("SESSION_RECORD_MISMATCH");
  }
  if (intent.state !== "awaiting_approval" || consent.state !== "requested") {
    throw new Error("CONSENT_NOT_PENDING");
  }
  if (
    !["user_recent_auth", "user_recent_auth_plus_device_local"].includes(
      consent.required_assurance,
    )
    || consent.required_assurance !== intent.required_assurance
  ) {
    throw new Error("ASSURANCE_MISMATCH");
  }
  if (
    Date.parse(intent.expires_at) <= now
    || Date.parse(consent.expires_at) <= now
  ) {
    throw new Error("APPROVAL_EXPIRED");
  }
  const nonceHash = `sha256:${createHash("sha256")
    .update(consent.challenge_nonce, "utf8")
    .digest("hex")}`;
  if (nonceHash !== consent.challenge_nonce_hash) {
    throw new Error("CHALLENGE_MISMATCH");
  }
  return {
    intent_digest: intent.intent_digest,
    consent_version: consent.consent_version,
    challenge_nonce: consent.challenge_nonce,
    decision,
  } as const;
}
