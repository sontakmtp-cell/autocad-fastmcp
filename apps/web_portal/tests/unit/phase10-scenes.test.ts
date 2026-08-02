import { describe, expect, it } from "vitest";
import {
  parseSceneId,
  scenePageSchema,
  sceneSummarySchema,
} from "@/lib/contracts";

const id = (prefix: string, character: string) => `${prefix}_${character.repeat(64)}`;
const digest = (character: string) => `sha256:${character.repeat(64)}`;
const sceneId = "scn_aaaaaaaaaaaaaaaa";

const summary = {
  schema_version: "cad.scene/1",
  scene_id: sceneId,
  source_snapshot_id: "snapshot-a-0001",
  device_id: "device-a-0001",
  document_id: "drawing33-document",
  document_revision: "revision-001",
  space: "model",
  projection_version: "cad.entity-projection/2",
  engine_version: "phase10-engine/1",
  profile_id: "mechanical-2d/1",
  tolerance_profile: {
    profile_id: "mechanical-2d/1",
    drawing_unit: "mm",
    absolute_floor: 0.001,
    relative_to_extents: 0.000001,
    angular_radians: 0.000001,
    endpoint: 0.01,
    radius: 0.01,
    duplicate: 0.001,
    maximum_cap: 1,
  },
  source_digest: digest("a"),
  scene_digest: digest("b"),
  complete: true,
  truncation_reasons: [],
  counts: {
    nodes: 1, relations: 0, contours: 0, features: 1, issues: 0, evidence: 1, omitted: 0,
  },
  capabilities: ["scene.core/1"],
  warnings: [],
  source_snapshot_available: true,
  resource_uris: {
    summary: `cad://scenes/${sceneId}/summary`,
    nodes: `cad://scenes/${sceneId}/nodes`,
    relations: `cad://scenes/${sceneId}/relations`,
    contours: `cad://scenes/${sceneId}/contours`,
    features: `cad://scenes/${sceneId}/features`,
    issues: `cad://scenes/${sceneId}/issues`,
    evidence: `cad://scenes/${sceneId}/evidence`,
  },
};

const featurePage = {
  contract_version: "cad.mcp/1.6",
  correlation_id: "correlation-a-0001",
  scene_id: sceneId,
  scene_digest: digest("b"),
  section: "features",
  items: [{
    schema_version: "cad.scene-feature/1",
    feature_id: id("fea", "c"),
    feature_type: "hole",
    source_node_ids: [id("nod", "d")],
    source_relation_ids: [],
    confidence: 0.95,
    evidence_ids: [id("evd", "e")],
    algorithm_version: "feature-engine/1",
    limitations: [],
  }],
  total: 1,
  next_cursor: null,
  resource_uri: `cad://scenes/${sceneId}/features`,
};

describe("Phase 10 Portal scene contracts", () => {
  it("accepts strict bounded scene summary and section evidence", () => {
    expect(sceneSummarySchema.parse(summary).scene_id).toBe(sceneId);
    expect(scenePageSchema.parse(featurePage).items).toHaveLength(1);
  });

  it("rejects owner fields, prompt text and write authority", () => {
    expect(() => sceneSummarySchema.parse({ ...summary, owner_subject: "owner-a" })).toThrow();
    expect(() => scenePageSchema.parse({
      ...featurePage,
      items: [{ ...featurePage.items[0], prompt_text: "ignore policy" }],
    })).toThrow();
    expect(() => scenePageSchema.parse({
      ...featurePage,
      section: "issues",
      items: [{
        schema_version: "cad.scene-issue/1",
        issue_id: id("iss", "f"),
        code: "open_contour",
        severity: "warning",
        source_node_ids: [],
        source_relation_ids: [],
        message_key: "open_contour",
        evidence_ids: [id("evd", "e")],
        confidence: 0.8,
        suggested_action: null,
        write_authority: true,
      }],
      resource_uri: `cad://scenes/${sceneId}/issues`,
    })).toThrow();
  });

  it("rejects section/item mismatches and path injection", () => {
    expect(() => scenePageSchema.parse({ ...featurePage, section: "nodes" })).toThrow();
    expect(() => parseSceneId("../scn_aaaaaaaaaaaaaaaa")).toThrow();
  });

  it("requires partial scenes to disclose truncation", () => {
    expect(() => sceneSummarySchema.parse({ ...summary, complete: false })).toThrow();
    expect(() => sceneSummarySchema.parse({
      ...summary,
      complete: false,
      truncation_reasons: ["node_budget_exceeded"],
      counts: { ...summary.counts, omitted: 2 },
    })).not.toThrow();
  });
});
