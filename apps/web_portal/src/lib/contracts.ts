import { z } from "zod";

const opaqueId = z.string().regex(/^[A-Za-z0-9_-]{8,128}$/);
const phase7PublicId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/);
const utcTimestamp = z.string().datetime({ offset: true });
const digest = z.string().regex(/^sha256:[0-9a-f]{64}$/);
const boundedText = z.string().min(1).max(512);
const version = z.number().int().min(0).max(2_147_483_647);

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

export const trustedEffectItemSchema = z.object({
  kind: z.enum(["create_entities", "erase_entities", "ensure_layers", "document_change"]),
  count: z.number().int().min(0).max(256),
  summary: boundedText,
}).strict();

const assuranceSchema = z.enum([
  "none",
  "device_local_confirmation",
  "user_recent_auth",
  "user_recent_auth_plus_device_local",
]);

const actorPrincipalSchema = z.object({
  issuer: boundedText,
  subject: boundedText,
}).strict();

const runtimePinsSchema = z.object({
  runtime_id: phase7PublicId,
  runtime_role: z.string().min(1).max(64),
  host_family: z.string().min(1).max(64),
  host_version: z.string().min(1).max(64),
  agent_package_id: phase7PublicId,
  agent_package_version: z.string().min(1).max(128),
  agent_package_hash: digest,
  host_package_id: phase7PublicId,
  host_package_version: z.string().min(1).max(128),
  host_package_hash: digest,
}).strict();

const policyPinsSchema = z.object({
  capability_manifest_hash: digest,
  operation_registry_hash: digest,
  registry_version: z.string().min(1).max(128),
  policy_version: z.string().min(1).max(128),
}).strict();

const executionIntentProjectionSchema = z.object({
  schema_version: z.literal("cad.execution-intent/1"),
  intent_id: phase7PublicId,
  intent_version: version.min(1),
  owner_subject: boundedText,
  action: z.enum(["program_commit", "rollback_commit"]),
  state: z.enum([
    "awaiting_approval",
    "ready",
    "released",
    "denied",
    "expired",
    "invalidated",
    "cancelled",
  ]),
  state_version: version,
  device_id: phase7PublicId,
  document_id: phase7PublicId,
  expected_document_revision: boundedText,
  program_id: phase7PublicId,
  program_revision: version.min(1),
  preview_id: phase7PublicId,
  risk_class: z.enum(["low", "medium", "high", "destructive"]),
  required_assurance: assuranceSchema,
  trusted_effect_summary: z.array(trustedEffectItemSchema).min(1).max(32),
  intent_digest: digest,
  created_at: utcTimestamp,
  expires_at: utcTimestamp,
  consent_id: phase7PublicId.nullable(),
  released_job_id: phase7PublicId.nullable(),
}).strict();

const fullExecutionIntentSchema = executionIntentProjectionSchema.extend({
  actor_principal: actorPrincipalSchema,
  device_identity_generation: version.min(1),
  device_key_thumbprint: digest,
  program_digest: digest,
  preview_digest: digest,
  preview_execution_digest: digest,
  preview_expires_at: utcTimestamp,
  deterministic_receipt_id: phase7PublicId,
  commit_execution_digest: digest,
  runtime_pins: runtimePinsSchema,
  policy_pins: policyPinsSchema,
  idempotency_key: z.string().min(1).max(256),
  request_hash: digest,
}).strict();

export const executionIntentSchema = z.union([
  fullExecutionIntentSchema,
  executionIntentProjectionSchema,
]);

const consentProjectionSchema = z.object({
  schema_version: z.literal("cad.consent/1"),
  consent_id: phase7PublicId,
  consent_version: version.min(1),
  owner_subject: boundedText,
  intent_id: phase7PublicId,
  intent_version: version.min(1),
  intent_digest: digest,
  required_assurance: assuranceSchema,
  state: z.enum(["requested", "approved", "denied", "expired", "invalidated", "consumed"]),
  state_version: version,
  challenge_nonce: z.string().min(16).max(512),
  challenge_nonce_hash: digest,
  requested_at: utcTimestamp,
  expires_at: utcTimestamp,
  decided_at: utcTimestamp.nullable(),
  decision_source: z.enum(["device_local", "portal_recent_auth"]).nullable(),
  consumed_at: utcTimestamp.nullable(),
}).strict();

const fullConsentRecordSchema = consentProjectionSchema.omit({
  challenge_nonce: true,
}).extend({
  decision_principal: actorPrincipalSchema.nullable(),
  decision_device_id: phase7PublicId.nullable(),
  decision_device_identity_generation: version.min(1).nullable(),
}).strict();

export const consentSchema = consentProjectionSchema;

export const portalConsentResponseSchema = z.union([
  consentProjectionSchema,
  z.object({
    consent: fullConsentRecordSchema,
    intent: fullExecutionIntentSchema,
    decision_nonce: z.string().min(32).max(256),
  }).strict(),
]);

export const consentDecisionResultSchema = z.object({
  status: z.enum(["approved", "denied"]),
  consent_id: phase7PublicId,
  consent_version: version.min(1),
  intent_id: phase7PublicId,
}).strict();

export const workflowRunSchema = z.object({
  run_id: z.string().min(1).max(160), skill_id: z.string().min(1).max(128),
  skill_version: z.string().min(1).max(32), state: z.string().min(1).max(64),
  state_version: z.number().int().nonnegative(), current_step_id: z.string().nullable().optional(),
  device_id: z.string().min(1).max(128), created_at: utcTimestamp, updated_at: utcTimestamp,
  pins: z.record(z.string(), z.unknown()), inputs: z.record(z.string(), z.unknown()),
}).passthrough();
export const workflowEventSchema = z.object({ sequence: z.number().int().positive(), event_type: z.string().min(1).max(128), created_at: utcTimestamp, payload: z.record(z.string(), z.unknown()) }).passthrough();
export const workflowDetailSchema = z.object({
  run: workflowRunSchema,
  steps: z.array(z.record(z.string(), z.unknown())).max(64),
  current_wait: z.record(z.string(), z.unknown()).nullable(),
  required_next_action: z.string().max(64).nullable(),
  events: z.array(workflowEventSchema).max(100),
  resource_uri: z.string().min(1).max(256),
}).strict();

const sceneId = z.string().regex(/^scn_[A-Za-z0-9_-]{16,120}$/);
const scenePublicId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/);
const sceneVersion = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/);
const sceneCode = z.string().regex(/^[a-z][a-z0-9._-]{0,63}$/);
const sceneCapability = z.string().regex(/^[a-z][a-z0-9._-]{0,62}\/[1-9][0-9]*$/);
const sceneNodeId = z.string().regex(/^nod_[0-9a-f]{64}$/);
const sceneRelationId = z.string().regex(/^rel_[0-9a-f]{64}$/);
const sceneContourId = z.string().regex(/^ctr_[0-9a-f]{64}$/);
const sceneFeatureId = z.string().regex(/^fea_[0-9a-f]{64}$/);
const sceneIssueId = z.string().regex(/^iss_[0-9a-f]{64}$/);
const sceneEvidenceId = z.string().regex(/^evd_[0-9a-f]{64}$/);
const finiteNumber = z.number().finite();
const confidence = finiteNumber.min(0).max(1);
export const sceneSectionSchema = z.enum([
  "nodes", "relations", "contours", "features", "issues", "evidence",
]);
const evidenceStrengthSchema = z.enum([
  "exact_source_geometry", "derived_exact", "bounded_heuristic", "unsupported",
]);
export const relationTypeSchema = z.enum([
  "connected_endpoint", "touch", "intersect", "overlap", "duplicate_geometry",
  "inside", "contains", "parallel", "perpendicular", "concentric", "aligned",
]);
export const featureTypeSchema = z.enum([
  "part", "hole", "repeated_hole_pattern", "concentric_group", "slot",
  "centerline_candidate", "annotation_link",
]);
export const issueCodeSchema = z.enum([
  "duplicate_geometry", "degenerate_geometry", "open_contour", "unsupported_geometry",
  "truncated_geometry", "orphan_annotation", "ambiguous_annotation",
  "inconsistent_repeated_feature",
]);
export const entityTypeSchema = z.string().regex(/^[A-Z][A-Z0-9_]{0,63}$/);

const pointSchema = z.object({ x: finiteNumber, y: finiteNumber }).strict();
const boundsSchema = z.object({ minimum: pointSchema, maximum: pointSchema }).strict()
  .refine((value) => (
    value.minimum.x <= value.maximum.x && value.minimum.y <= value.maximum.y
  ), "bounds minimum must not exceed maximum");
const lineGeometrySchema = z.object({
  kind: z.literal("line"), start: pointSchema, end: pointSchema,
}).strict();
const circleGeometrySchema = z.object({
  kind: z.literal("circle"), center: pointSchema, radius: finiteNumber.positive(),
}).strict();
const arcGeometrySchema = z.object({
  kind: z.literal("arc"),
  center: pointSchema,
  radius: finiteNumber.positive(),
  start_angle_radians: finiteNumber,
  end_angle_radians: finiteNumber,
}).strict();
const polylineGeometrySchema = z.object({
  kind: z.literal("polyline"),
  vertices: z.array(pointSchema).min(2).max(4096),
  bulges: z.array(finiteNumber).max(4096),
  closed: z.boolean(),
  elevation: finiteNumber,
}).strict().refine(
  (value) => value.bulges.length === 0 || value.bulges.length === value.vertices.length,
  "polyline bulges must match vertices",
);
const geometrySchema = z.discriminatedUnion("kind", [
  lineGeometrySchema, circleGeometrySchema, arcGeometrySchema, polylineGeometrySchema,
]);
const finiteMetricsSchema = z.record(sceneCode, finiteNumber);

export const sceneNodeSchema = z.object({
  schema_version: z.literal("cad.scene-node/1"),
  node_id: sceneNodeId,
  source_entity_id: scenePublicId,
  entity_type: entityTypeSchema,
  layer: z.string().min(1).max(255),
  space: z.enum(["model", "paper"]),
  bounds: boundsSchema.nullable(),
  geometry: geometrySchema.nullable(),
  geometry_status: z.enum([
    "exact", "bounded_projection", "truncated", "unsupported", "unavailable", "invalid",
  ]),
  fingerprint: digest,
  source_runtime: sceneCode,
  source_capabilities: z.array(sceneCapability).max(32),
}).strict().refine((value) => (
  ["exact", "bounded_projection"].includes(value.geometry_status)
    ? value.geometry !== null
    : !["unsupported", "unavailable", "invalid"].includes(value.geometry_status)
      || value.geometry === null
), "geometry does not match geometry_status");

export const sceneRelationSchema = z.object({
  schema_version: z.literal("cad.scene-relation/1"),
  relation_id: sceneRelationId,
  relation_type: relationTypeSchema,
  source_node_ids: z.array(sceneNodeId).min(2).max(8),
  directionality: z.enum(["symmetric", "directed"]),
  evidence_strength: evidenceStrengthSchema,
  confidence,
  metrics: finiteMetricsSchema,
  tolerance_used: finiteNumber.positive(),
  algorithm_version: sceneVersion,
  evidence_ids: z.array(sceneEvidenceId).min(1).max(16),
}).strict().refine(
  (value) => new Set(value.source_node_ids).size === value.source_node_ids.length,
  "relation nodes must be distinct",
);

export const sceneContourSchema = z.object({
  schema_version: z.literal("cad.scene-contour/1"),
  contour_id: sceneContourId,
  source_node_ids: z.array(sceneNodeId).min(1).max(4096),
  closed: z.boolean(),
  bounds: boundsSchema,
  signed_area: finiteNumber.nullable(),
  orientation: z.enum(["clockwise", "counterclockwise", "undefined"]),
  algorithm_version: sceneVersion,
  evidence_ids: z.array(sceneEvidenceId).min(1).max(16),
}).strict();

export const sceneFeatureSchema = z.object({
  schema_version: z.literal("cad.scene-feature/1"),
  feature_id: sceneFeatureId,
  feature_type: featureTypeSchema,
  source_node_ids: z.array(sceneNodeId).min(1).max(256),
  source_relation_ids: z.array(sceneRelationId).max(256),
  confidence,
  evidence_ids: z.array(sceneEvidenceId).min(1).max(32),
  algorithm_version: sceneVersion,
  limitations: z.array(sceneCode).max(16),
}).strict();

export const sceneIssueSchema = z.object({
  schema_version: z.literal("cad.scene-issue/1"),
  issue_id: sceneIssueId,
  code: issueCodeSchema,
  severity: z.enum(["info", "warning", "error"]),
  source_node_ids: z.array(sceneNodeId).max(256),
  source_relation_ids: z.array(sceneRelationId).max(256),
  message_key: sceneCode,
  evidence_ids: z.array(sceneEvidenceId).min(1).max(32),
  confidence,
  suggested_action: sceneCode.nullable(),
  write_authority: z.literal(false),
}).strict();

export const sceneEvidenceSchema = z.object({
  schema_version: z.literal("cad.scene-evidence/1"),
  evidence_id: sceneEvidenceId,
  evidence_type: sceneCode,
  evidence_strength: evidenceStrengthSchema,
  source_node_ids: z.array(sceneNodeId).max(64),
  source_entity_ids: z.array(scenePublicId).max(64),
  metrics: finiteMetricsSchema,
  algorithm_version: sceneVersion,
  limitations: z.array(sceneCode).max(16),
}).strict();

const sceneCountsSchema = z.object({
  nodes: z.number().int().min(0).max(10_000),
  relations: z.number().int().min(0).max(200_000),
  contours: z.number().int().min(0).max(20_000),
  features: z.number().int().min(0).max(50_000),
  issues: z.number().int().min(0).max(50_000),
  evidence: z.number().int().min(0).max(200_000),
  omitted: z.number().int().min(0).max(500_000),
}).strict();
const sceneResourceUri = z.string().regex(
  /^cad:\/\/scenes\/[A-Za-z0-9._-]+\/(?:summary|nodes|relations|contours|features|issues|evidence)$/,
).max(256);

export const sceneSummarySchema = z.object({
  schema_version: z.literal("cad.scene/1"),
  scene_id: sceneId,
  source_snapshot_id: scenePublicId,
  device_id: scenePublicId,
  document_id: scenePublicId,
  document_revision: z.string().regex(/^[!-~]{1,128}$/),
  space: z.literal("model"),
  projection_version: z.literal("cad.entity-projection/2"),
  engine_version: sceneVersion,
  profile_id: z.literal("mechanical-2d/1"),
  tolerance_profile: z.object({
    profile_id: z.literal("mechanical-2d/1"),
    drawing_unit: z.string().min(1).max(32),
    absolute_floor: finiteNumber.positive(),
    relative_to_extents: finiteNumber.positive(),
    angular_radians: finiteNumber.positive(),
    endpoint: finiteNumber.positive(),
    radius: finiteNumber.positive(),
    duplicate: finiteNumber.positive(),
    maximum_cap: finiteNumber.positive(),
  }).strict(),
  source_digest: digest,
  scene_digest: digest,
  complete: z.boolean(),
  truncation_reasons: z.array(sceneCode).max(16),
  counts: sceneCountsSchema,
  capabilities: z.array(sceneCapability).max(64),
  warnings: z.array(sceneCode).max(64),
  source_snapshot_available: z.boolean(),
  resource_uris: z.object({
    summary: sceneResourceUri,
    nodes: sceneResourceUri,
    relations: sceneResourceUri,
    contours: sceneResourceUri,
    features: sceneResourceUri,
    issues: sceneResourceUri,
    evidence: sceneResourceUri,
  }).strict(),
}).strict().superRefine((value, context) => {
  const invalidComplete = value.complete
    ? value.truncation_reasons.length > 0 || value.counts.omitted > 0
    : value.truncation_reasons.length === 0;
  if (invalidComplete) context.addIssue({ code: z.ZodIssueCode.custom, message: "invalid completeness" });
  const capped = [
    value.tolerance_profile.absolute_floor,
    value.tolerance_profile.endpoint,
    value.tolerance_profile.radius,
    value.tolerance_profile.duplicate,
  ];
  if (capped.some((item) => item > value.tolerance_profile.maximum_cap)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "linear tolerance exceeds cap" });
  }
});

export const sceneListSchema = z.object({
  scenes: z.array(sceneSummarySchema).max(1000),
}).strict();

export const sceneQueryInputSchema = z.object({
  scene_id: sceneId,
  section: sceneSectionSchema,
  entity_types: z.array(entityTypeSchema).max(16),
  relation_types: z.array(relationTypeSchema).max(16),
  feature_types: z.array(featureTypeSchema).max(16),
  issue_codes: z.array(issueCodeSchema).max(16),
  source_entity_ids: z.array(scenePublicId).max(64),
  confidence_min: confidence.optional(),
  cursor: z.string().regex(/^[A-Za-z0-9_.-]{1,512}$/).optional(),
  limit: z.number().int().min(1).max(200),
}).strict().superRefine((value, context) => {
  const filters = {
    nodes: value.entity_types.length > 0,
    relations: value.relation_types.length > 0,
    features: value.feature_types.length > 0,
    issues: value.issue_codes.length > 0,
    contours: false,
    evidence: false,
  };
  if (Object.entries(filters).some(([section, enabled]) => enabled && section !== value.section)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "filter does not match section" });
  }
  if (value.confidence_min !== undefined
    && !["relations", "features", "issues"].includes(value.section)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "confidence does not match section" });
  }
  for (const items of [
    value.entity_types, value.relation_types, value.feature_types,
    value.issue_codes, value.source_entity_ids,
  ]) {
    if (new Set(items).size !== items.length) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: "duplicate scene filter" });
    }
  }
});

const sceneItemSchema = z.discriminatedUnion("schema_version", [
  sceneNodeSchema, sceneRelationSchema, sceneContourSchema,
  sceneFeatureSchema, sceneIssueSchema, sceneEvidenceSchema,
]);
const sectionItemVersions: Record<z.infer<typeof sceneSectionSchema>, string> = {
  nodes: "cad.scene-node/1",
  relations: "cad.scene-relation/1",
  contours: "cad.scene-contour/1",
  features: "cad.scene-feature/1",
  issues: "cad.scene-issue/1",
  evidence: "cad.scene-evidence/1",
};
export const scenePageSchema = z.object({
  contract_version: z.literal("cad.mcp/1.6"),
  correlation_id: scenePublicId,
  scene_id: sceneId,
  scene_digest: digest,
  section: sceneSectionSchema,
  items: z.array(sceneItemSchema).max(200),
  total: z.number().int().min(0).max(500_000),
  next_cursor: z.string().regex(/^[A-Za-z0-9_.-]{1,512}$/).nullable(),
  resource_uri: z.string().regex(
    /^cad:\/\/scenes\/[A-Za-z0-9._-]+\/(?:nodes|relations|contours|features|issues|evidence)(?:\?[A-Za-z0-9._~=&%-]{1,512})?$/,
  ).max(768),
}).strict().refine(
  (value) => value.items.every(
    (item) => item.schema_version === sectionItemVersions[value.section],
  ),
  "scene items do not match section",
);

export type Device = z.infer<typeof deviceSchema>;
export type Pairing = z.infer<typeof pairingSchema>;
export type ExecutionBinding = z.infer<typeof executionBindingSchema>;
export type ProgramRevision = z.infer<typeof programRevisionSchema>;
export type ProgramPreview = z.infer<typeof previewSchema>;
export type ProgramValidation = z.infer<typeof validationSchema>;
export type ProgramReceipt = z.infer<typeof receiptSchema>;
export type Phase6Job = z.infer<typeof phase6JobSchema>;
export type ExecutionIntent = z.infer<typeof executionIntentSchema>;
export type Consent = z.infer<typeof consentSchema>;
export type ConsentDecisionResult = z.infer<typeof consentDecisionResultSchema>;
export type PortalConsentResponse = z.infer<typeof portalConsentResponseSchema>;
export type WorkflowRun = z.infer<typeof workflowRunSchema>;
export type WorkflowDetail = z.infer<typeof workflowDetailSchema>;
export type SceneSummary = z.infer<typeof sceneSummarySchema>;
export type ScenePage = z.infer<typeof scenePageSchema>;
export type SceneSection = z.infer<typeof sceneSectionSchema>;
export type SceneItem = z.infer<typeof sceneItemSchema>;
export type SceneQueryInput = z.infer<typeof sceneQueryInputSchema>;

export function parseOpaqueId(value: string): string {
  return opaqueId.parse(value);
}

export function parsePhase7Id(value: string): string {
  return phase7PublicId.parse(value);
}

export function parseSceneId(value: string): string {
  return sceneId.parse(value);
}
