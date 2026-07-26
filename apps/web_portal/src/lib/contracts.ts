import { z } from "zod";

const opaqueId = z.string().regex(/^[A-Za-z0-9_-]{8,128}$/);
const utcTimestamp = z.string().datetime({ offset: true });

export const runtimeSchema = z.object({
  label: z.string().min(1),
  role: z.enum(["primary", "compatibility", "fallback", "unsupported"]),
  health: z.enum(["ready", "degraded", "offline", "unsupported"]),
});

export const deviceSchema = z.object({
  id: opaqueId,
  name: z.string().min(1).max(120),
  is_default: z.boolean(),
  connected: z.boolean(),
  last_seen_at: utcTimestamp.nullable(),
  runtime: runtimeSchema.nullable(),
});

export const devicesSchema = z.object({
  devices: z.array(deviceSchema),
});

export const pairingSchema = z.object({
  id: opaqueId,
  device_name: z.string().min(1).max(120),
  requested_at: utcTimestamp,
  expires_at: utcTimestamp,
  status: z.enum(["pending", "confirmed", "denied", "expired"]),
});

export const mutationResultSchema = z.object({
  status: z.enum(["confirmed", "denied", "revoked"]),
});

export type Device = z.infer<typeof deviceSchema>;
export type Pairing = z.infer<typeof pairingSchema>;

export function parseOpaqueId(value: string): string {
  return opaqueId.parse(value);
}
