import "server-only";
import { z } from "zod";
import {
  deviceSchema,
  devicesSchema,
  consentDecisionResultSchema,
  consentSchema,
  executionIntentSchema,
  mutationResultSchema,
  pairingSchema,
  parseOpaqueId,
  parsePhase7Id,
  portalConsentResponseSchema,
  phase6JobSchema,
  phase6ReleaseStatusSchema,
  previewSchema,
  programRevisionSchema,
  receiptSchema,
  validationSchema,
  type Device,
  type Consent,
  type ConsentDecisionResult,
  type ExecutionIntent,
  type Phase6Job,
  type Phase6ReleaseStatus,
  type Pairing,
  type ProgramPreview,
  type ProgramReceipt,
  type ProgramRevision,
  type ProgramValidation,
} from "./contracts";
import { portalEnv } from "./env";
import type { PortalSession } from "./session";

export class GatewayError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
  }
}

export class GatewayClient {
  constructor(private readonly session: PortalSession) {}

  async getPhase6ReleaseStatus(): Promise<Phase6ReleaseStatus> {
    return this.request(
      "/api/portal/v1/phase6/status",
      phase6ReleaseStatusSchema,
    );
  }

  async listDevices(): Promise<Device[]> {
    return (await this.request("/api/portal/v1/devices", devicesSchema)).devices;
  }

  async getDevice(deviceId: string): Promise<Device> {
    return this.request(
      `/api/portal/v1/devices/${encodeURIComponent(parseOpaqueId(deviceId))}`,
      deviceSchema,
    );
  }

  async getPairing(pairingId: string): Promise<Pairing> {
    return this.request(
      `/api/portal/v1/pairings/${encodeURIComponent(parseOpaqueId(pairingId))}`,
      pairingSchema,
    );
  }

  async confirmPairing(pairingId: string): Promise<void> {
    await this.request(
      `/api/portal/v1/pairings/${encodeURIComponent(parseOpaqueId(pairingId))}/confirm`,
      mutationResultSchema,
      "POST",
    );
  }

  async denyPairing(pairingId: string): Promise<void> {
    await this.request(
      `/api/portal/v1/pairings/${encodeURIComponent(parseOpaqueId(pairingId))}/deny`,
      mutationResultSchema,
      "POST",
    );
  }

  async revokeDevice(deviceId: string): Promise<void> {
    await this.request(
      `/api/portal/v1/devices/${encodeURIComponent(parseOpaqueId(deviceId))}/revoke`,
      mutationResultSchema,
      "POST",
    );
  }

  async getProgram(programId: string, revision: number): Promise<ProgramRevision> {
    if (!Number.isSafeInteger(revision) || revision < 1) {
      throw new GatewayError(404, "NOT_FOUND");
    }
    return this.request(
      `/api/portal/v1/programs/${encodeURIComponent(parseOpaqueId(programId))}`
      + `/revisions/${revision}`,
      programRevisionSchema,
    );
  }

  async getPreview(previewId: string): Promise<ProgramPreview> {
    return this.request(
      `/api/portal/v1/previews/${encodeURIComponent(parseOpaqueId(previewId))}`,
      previewSchema,
    );
  }

  async getValidation(validationId: string): Promise<ProgramValidation> {
    return this.request(
      `/api/portal/v1/validations/${encodeURIComponent(parseOpaqueId(validationId))}`,
      validationSchema,
    );
  }

  async getReceipt(receiptId: string): Promise<ProgramReceipt> {
    return this.request(
      `/api/portal/v1/receipts/${encodeURIComponent(parseOpaqueId(receiptId))}`,
      receiptSchema,
    );
  }

  async getJob(jobId: string): Promise<Phase6Job> {
    return this.request(
      `/api/portal/v1/jobs/${encodeURIComponent(parseOpaqueId(jobId))}`,
      phase6JobSchema,
    );
  }

  async getIntent(intentId: string): Promise<ExecutionIntent> {
    return this.request(
      `/api/portal/v1/intents/${encodeURIComponent(parsePhase7Id(intentId))}`,
      executionIntentSchema,
    );
  }

  async getConsent(consentId: string): Promise<Consent> {
    const response = await this.request(
      `/api/portal/v1/consents/${encodeURIComponent(parsePhase7Id(consentId))}`,
      portalConsentResponseSchema,
    );
    if (!("consent" in response)) {
      return response;
    }
    const value = response.consent;
    return consentSchema.parse({
      schema_version: value.schema_version,
      consent_id: value.consent_id,
      consent_version: value.consent_version,
      owner_subject: value.owner_subject,
      intent_id: value.intent_id,
      intent_version: value.intent_version,
      intent_digest: value.intent_digest,
      required_assurance: value.required_assurance,
      state: value.state,
      state_version: value.state_version,
      challenge_nonce: response.decision_nonce,
      challenge_nonce_hash: value.challenge_nonce_hash,
      requested_at: value.requested_at,
      expires_at: value.expires_at,
      decided_at: value.decided_at,
      decision_source: value.decision_source,
      consumed_at: value.consumed_at,
    });
  }

  async decideConsent(
    consentId: string,
    decision: "approve" | "deny",
    body: {
      intent_digest: string;
      consent_version: number;
      challenge_nonce: string;
      decision: "approve" | "deny";
    },
  ): Promise<ConsentDecisionResult> {
    return this.request(
      `/api/portal/v1/consents/${encodeURIComponent(parsePhase7Id(consentId))}/${decision}`,
      consentDecisionResultSchema,
      "POST",
      body,
    );
  }

  private async request<T>(
    path: string,
    schema: z.ZodType<T>,
    method: "GET" | "POST" = "GET",
    body?: Record<string, unknown>,
  ): Promise<T> {
    const base = portalEnv().PORTAL_GATEWAY_BASE_URL.replace(/\/+$/, "");
    const response = await fetch(`${base}${path}`, {
      method,
      headers: {
        accept: "application/json",
        authorization: `Bearer ${this.session.accessToken}`,
        ...(body ? {
          "content-type": "application/json",
          origin: new URL(portalEnv().PORTAL_PUBLIC_ORIGIN).origin,
          ...(typeof body.challenge_nonce === "string"
            ? { "x-csrf-token": body.challenge_nonce }
            : {}),
        } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
      redirect: "error",
    });

    if (!response.ok) {
      let gatewayCode: string | undefined;
      try {
        const parsed = z.object({
          code: z.string().min(1).max(128).optional(),
          error: z.string().min(1).max(128).optional(),
        })
          .passthrough()
          .safeParse(await response.json());
        gatewayCode = parsed.success
          ? parsed.data.code ?? parsed.data.error
          : undefined;
      } catch {
        gatewayCode = undefined;
      }
      if ([401, 403].includes(response.status) && gatewayCode === "recent_auth_required") {
        throw new GatewayError(403, "RECENT_AUTH_REQUIRED", gatewayCode);
      }
      // Keep 403 and 404 indistinguishable to callers that guessed another owner's ID.
      const safeStatus = response.status === 403 ? 404 : response.status;
      throw new GatewayError(
        safeStatus,
        safeStatus === 404 ? "NOT_FOUND" : "GATEWAY_FAILED",
        gatewayCode,
      );
    }
    return schema.parse(await response.json());
  }
}
