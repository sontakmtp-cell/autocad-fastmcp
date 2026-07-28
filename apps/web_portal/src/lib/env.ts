import "server-only";
import { z } from "zod";
import type { Phase6ReleaseStatus } from "./contracts";

const absoluteHttpUrl = z.string().url().refine((value) => {
  const url = new URL(value);
  return url.protocol === "https:"
    || (url.protocol === "http:"
      && ["127.0.0.1", "localhost", "::1"].includes(url.hostname));
}, "must use HTTPS, except for exact loopback development URLs");

const schema = z.object({
  PORTAL_PUBLIC_ORIGIN: absoluteHttpUrl,
  PORTAL_GATEWAY_BASE_URL: absoluteHttpUrl,
  PORTAL_SESSION_SECRET: z.string().min(32),
  PORTAL_OIDC_ISSUER: absoluteHttpUrl,
  PORTAL_OIDC_CLIENT_ID: z.string().min(1),
  PORTAL_OIDC_CLIENT_SECRET: z.string().optional(),
  PORTAL_OIDC_AUDIENCE: z.string().min(1),
  PORTAL_OIDC_SCOPES: z.string().default(
    "openid profile email autocad.read autocad.write autocad.device.manage",
  ),
  PORTAL_PHASE6_UI_ENABLED: z.enum(["true", "false"]).default("false"),
  PORTAL_MANAGED_WRITE_UI_ENABLED: z.enum(["true", "false"]).default("false"),
  PORTAL_MANAGED_WRITE_KILL_SWITCH: z.enum(["true", "false"]).default("true"),
  PORTAL_PHASE7_UI_ENABLED: z.enum(["true", "false"]).default("false"),
  PORTAL_RECENT_AUTH_APPROVAL_ENABLED: z.enum(["true", "false"]).default("false"),
  PORTAL_RECENT_AUTH_MAX_AGE_SECONDS: z.coerce.number().int().min(60).max(3600).default(300),
});

export type PortalEnv = z.infer<typeof schema>;

export function portalEnv(): PortalEnv {
  return schema.parse(process.env);
}

export type Phase6UiState = {
  phase6Enabled: boolean;
  managedWriteEnabled: boolean;
  killSwitchActive: boolean;
  gatewayStateAvailable: boolean;
};

export function phase6UiState(
  gatewayState?: Phase6ReleaseStatus,
): Phase6UiState {
  const env = portalEnv();
  const gatewayStateAvailable = gatewayState !== undefined;
  const phase6Enabled = gatewayStateAvailable
    && gatewayState.program_v0_enabled
    && env.PORTAL_PHASE6_UI_ENABLED === "true";
  const killSwitchActive = !gatewayStateAvailable
    || gatewayState.kill_switch_active
    || !gatewayState.managed_write_enabled
    || env.PORTAL_MANAGED_WRITE_KILL_SWITCH !== "false";
  return {
    phase6Enabled,
    killSwitchActive,
    gatewayStateAvailable,
    managedWriteEnabled: phase6Enabled
      && env.PORTAL_MANAGED_WRITE_UI_ENABLED === "true"
      && !killSwitchActive,
  };
}

export type Phase7UiState = {
  phase7Enabled: boolean;
  recentAuthApprovalEnabled: boolean;
};

export function phase7UiState(): Phase7UiState {
  const env = portalEnv();
  const phase7Enabled = env.PORTAL_PHASE7_UI_ENABLED === "true";
  return {
    phase7Enabled,
    recentAuthApprovalEnabled: phase7Enabled
      && env.PORTAL_RECENT_AUTH_APPROVAL_ENABLED === "true",
  };
}
