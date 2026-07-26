import "server-only";
import { z } from "zod";
import {
  deviceSchema,
  devicesSchema,
  mutationResultSchema,
  pairingSchema,
  parseOpaqueId,
  type Device,
  type Pairing,
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
