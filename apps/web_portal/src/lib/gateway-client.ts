import "server-only";
import { z } from "zod";
import {
  deviceSchema,
  devicesSchema,
  mutationResultSchema,
  pairingSchema,
  parseOpaqueId,
  phase6JobSchema,
  phase6ReleaseStatusSchema,
  previewSchema,
  programRevisionSchema,
  receiptSchema,
  validationSchema,
  type Device,
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

  private async request<T>(
    path: string,
    schema: z.ZodType<T>,
    method: "GET" | "POST" = "GET",
  ): Promise<T> {
    const base = portalEnv().PORTAL_GATEWAY_BASE_URL.replace(/\/+$/, "");
    const response = await fetch(`${base}${path}`, {
      method,
      headers: {
        accept: "application/json",
        authorization: `Bearer ${this.session.accessToken}`,
      },
      cache: "no-store",
      redirect: "error",
    });

    if (!response.ok) {
      // Keep 403 and 404 indistinguishable to callers that guessed another owner's ID.
      const safeStatus = response.status === 403 ? 404 : response.status;
      throw new GatewayError(safeStatus, safeStatus === 404 ? "NOT_FOUND" : "GATEWAY_FAILED");
    }
    return schema.parse(await response.json());
  }
}
