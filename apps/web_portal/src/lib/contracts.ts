import { z } from "zod";

const opaqueId = z.string().regex(/^[A-Za-z0-9_-]{8,128}$/);
const utcTimestamp = z.string().datetime({ offset: true });
const digest = z.string().regex(/^sha256:[0-9a-f]{64}$/);
const boundedText = z.string().min(1).max(512);

export const phase6ReleaseStatusSchema = z.object({
  program_v0_enabled: z.boolean(),
  managed_write_enabled: z.boolean(),
  kill_switch_active: z.boolean(),
});

export type Phase6ReleaseStatus = z.infer<typeof phase6ReleaseStatusSchema>;

export const executionBindingSchema = z.object({
  runtime_id: boundedText,
  runtime_role: boundedText,
  host_family: boundedText,
  host_version: boundedText,
  package_id: boundedText,
  package_version: boundedText,
  package_hash: digest,
  capability_manifest_hash: digest,
  operation_registry_hash: digest,
  registry_version: boundedText,
  policy_version: boundedText,
});

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

export const programRevisionSchema = z.object({
  program_id: opaqueId,
  program_revision: z.number().int().positive(),
  device_id: opaqueId,
  document_id: boundedText,
  source_snapshot_id: opaqueId,
  expected_document_revision: boundedText,
  schema_version: z.literal("cad.program/0.2"),
  program_digest: digest,
  risk_class: z.literal("low"),
  missing_capabilities: z.array(boundedText).max(64),
  pins: executionBindingSchema,
  created_at: utcTimestamp,
});

export const previewSchema = z.object({
  preview_id: opaqueId,
  program_id: opaqueId,
  program_revision: z.number().int().positive(),
  job_id: opaqueId,
  program_digest: digest,
  execution_digest: digest,
  preview_digest: digest,
  binding_digest: digest,
  document_id: boundedText,
  expected_document_revision: boundedText,
  runtime_id: boundedText,
  runtime_role: boundedText,
  host_family: boundedText,
  host_version: boundedText,
  package_id: boundedText,
  package_version: boundedText,
  package_hash: digest,
  capability_manifest_hash: digest,
  operation_registry_hash: digest,
  registry_version: boundedText,
  policy_version: boundedText,
  planned_operation_count: z.number().int().nonnegative(),
  planned_entity_count: z.number().int().nonnegative(),
  planned_layer_count: z.number().int().nonnegative(),
  validation: z.record(z.string(), z.unknown()),
  expires_at: utcTimestamp,
  invalidated_reason: z.string().max(256).nullable(),
  created_at: utcTimestamp,
});

export const validationSchema = z.object({
  validation_id: opaqueId,
  program_id: opaqueId,
  program_revision: z.number().int().positive(),
  receipt_id: opaqueId,
  job_id: opaqueId,
  execution_digest: digest,
  binding_digest: digest,
  document_revision: boundedText,
  passed: z.boolean(),
  report: z.record(z.string(), z.unknown()),
  created_at: utcTimestamp,
});

export const receiptSchema = z.object({
  receipt_id: opaqueId,
  program_id: opaqueId,
  program_revision: z.number().int().positive(),
  preview_id: opaqueId,
  job_id: opaqueId,
  program_digest: digest,
  execution_digest: digest,
  receipt_digest: digest,
  preview_execution_digest: digest,
  binding_digest: digest,
  document_id: boundedText,
  document_revision_before: boundedText,
  document_revision_after: boundedText,
  runtime_id: boundedText,
  package_hash: digest,
  capability_manifest_hash: digest,
  operation_registry_hash: digest,
  policy_version: boundedText,
  effect_summary: z.record(z.string(), z.unknown()),
  durable_receipt: z.record(z.string(), z.unknown()),
  created_at: utcTimestamp,
});

export const phase6JobSchema = z.object({
  job_id: opaqueId,
  device_id: opaqueId,
  kind: z.enum(["program_preview", "program_commit", "program_validate"]),
  effect_class: z.enum(["read", "write"]),
  state: z.enum([
    "queued",
    "dispatched",
    "acknowledged",
    "running",
    "reconnect_pending",
    "cancel_requested",
    "outcome_unknown",
    "succeeded",
    "failed",
    "cancelled",
    "needs_attention",
  ]),
  progress: z.record(z.string(), z.unknown()).nullable(),
  result: z.record(z.string(), z.unknown()).nullable(),
  error_code: z.string().max(256).nullable(),
  created_at: utcTimestamp,
  updated_at: utcTimestamp,
});

export type Device = z.infer<typeof deviceSchema>;
export type Pairing = z.infer<typeof pairingSchema>;
export type ExecutionBinding = z.infer<typeof executionBindingSchema>;
export type ProgramRevision = z.infer<typeof programRevisionSchema>;
export type ProgramPreview = z.infer<typeof previewSchema>;
export type ProgramValidation = z.infer<typeof validationSchema>;
export type ProgramReceipt = z.infer<typeof receiptSchema>;
export type Phase6Job = z.infer<typeof phase6JobSchema>;

export function parseOpaqueId(value: string): string {
  return opaqueId.parse(value);
}
