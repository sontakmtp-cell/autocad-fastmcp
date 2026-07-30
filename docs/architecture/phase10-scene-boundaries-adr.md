# Phase 10 Scene Graph boundaries ADR

Status: accepted for local Phase 10 implementation on 2026-07-30.

Baseline: `48365bccccb9c1214a4447072410f99ac8361dc3`, containing PR #13 at
`2217bd0387bcc8e5f2a4d9c0235c58e52dd3eab7`.

## Evidence classification

- Verified: `CadEntity` and `CadQueryInput` are the existing entity paging
  boundary in `services/gateway/src/autocad_gateway/contracts.py`.
- Verified: durable snapshot lookup is owner filtered in
  `SqliteRepository.get_snapshot`; local snapshots are bounded and TTL based in
  `BoundedSnapshotStore`.
- Verified: Phase 9 workflow definitions are strict, immutable, typed DAGs in
  `phase9_contracts.py`; workflow actions do not call public MCP tools.
- Verified: R25 currently projects exact LINE, CIRCLE and straight
  LWPOLYLINE geometry in `AutoCadEntitySnapshotOperations.TryGetGeometry`.
  ARC and polyline bulge/elevation/normal are not yet projected.
- Verified: the current entity cursor is bounded and filter-bound but is not
  authenticated.
- New Phase 10 decision: the rules below are the accepted implementation
  boundary and unblock slices 10.1 onward.
- Unknown until live evidence: the three R25 fixtures, cross-runtime parity and
  post-restart no-write behavior.

## Authority boundary

An immutable drawing snapshot remains the source of truth. A scene is an
immutable, owner-scoped, revision-pinned derived artifact. Scene relations,
features, confidence and issue reports are evidence only:

```text
feature_id != entity authority
confidence != approval
scene report != commit proof
```

Any later CAD change must resolve exact source entity references, revalidate
the same document/revision and use the existing Phase 6–9
prepare/preview/trusted-approval/commit/validate/recovery path. Scene code must
not weaken or fork that path.

## Component ownership

- FastMCP owns only two public tools, read-scope binding, safe errors and
  bounded resources.
- Gateway owns owner authorization, snapshot lookup, flags, budgets,
  deduplication, persistence, signed cursors, redaction and workflow ports.
- `cad_core.scene` owns pure deterministic projection, indexing, relations,
  contours, features, issues and canonical IDs/digests. It imports neither
  FastMCP, SQLite, OAuth, Autodesk nor Agent code.
- Desktop Agent only forwards bounded source projection and capabilities.
- Managed R25 Host only emits exact source facts and explicit unsupported or
  truncated reasons.
- ezdxf is an offline/cross-runtime adapter outside the pure scene engine.
- LT keeps its existing read path and reports missing scene capabilities. LT
  write remains disabled.

## Public surface

Public contract version is additive `cad.mcp/1.6`.

Exactly two tools are added under explicit Phase 10 flags:

- `cad_build_scene`: build or reuse one immutable scene from an owner-scoped
  snapshot.
- `cad_query_scene`: page one typed section with closed filters.

Both require only `autocad.read`, are read-only, non-destructive and
idempotent. Existing `cad_query` is unchanged. There is no tool per relation or
feature and no arbitrary query AST, expression, regex, SQL, URL, path or code.

Owner-scoped resources are:

```text
cad://scenes/{scene_id}/summary
cad://scenes/{scene_id}/nodes
cad://scenes/{scene_id}/relations
cad://scenes/{scene_id}/contours
cad://scenes/{scene_id}/features
cad://scenes/{scene_id}/issues
cad://scenes/{scene_id}/evidence
```

Section results are bounded pages; tool results contain only identity,
revision, digest, completeness, counts, warnings and resource links.

## Contracts, IDs and digests

The strict family is `cad.scene/1`, `cad.scene-node/1`,
`cad.scene-relation/1`, `cad.scene-contour/1`, `cad.scene-feature/1`,
`cad.scene-issue/1`, `cad.scene-evidence/1` and `cad.scene-query/1`.
Models are frozen, strict, extra-forbid and bounded. Floats must be finite;
confidence is within 0..1.

Reuse `autocad_contracts.agent_protocol.canonical_json`; do not create another
JSON canonicalizer.

- `scene_id` is an opaque server-generated `scn_*` identifier.
- Node ID derives from the source entity ID.
- Relation ID derives from relation type, directionality discriminator,
  canonical metrics and sorted node IDs for symmetric relations.
- Contour, feature and issue IDs derive from their typed code, sorted source
  evidence and algorithm/detector version.
- Stable IDs use a domain-separated full SHA-256 digest. Entity/key/page order
  must not affect them.
- `source_digest` binds snapshot, device, document/revision, space, canonical
  projections/fingerprints, source capabilities, analysis profile, tolerance
  and build options.
- `scene_digest` additionally binds schema, engine/algorithm versions and all
  sorted canonical scene sections. It excludes owner, opaque scene ID,
  timestamps, correlation and resource URIs.

The server computes every digest. Same owner plus the same source/profile/
engine/tolerance/options key reuses the exact scene. A conflicting payload is
rejected; an engine/profile change creates a new scene.

## Geometry and tolerance

Scene v0 is planar WCS-normalized XY model space. Model and paper space never
share a component graph.

Tier A source support:

- LINE: start/end;
- CIRCLE: center/radius and planar normal evidence;
- LWPOLYLINE: ordered vertices with bulge, closed, elevation and planar normal;
- ARC: center/radius/start/end angles and planar normal.

Non-finite, non-planar or unnormalizable entities are explicit
`invalid`/`unsupported`, never silently approximated. Geometry status is one
of `exact`, `bounded_projection`, `truncated`, `unsupported`, `unavailable` or
`invalid`.

The server-selected `mechanical-2d/1` tolerance profile is versioned and binds
drawing-unit evidence, absolute floor, relative-to-extents term, angular,
endpoint, radius and duplicate tolerances, plus a maximum cap. Clients cannot
send arbitrary epsilon values.

Exact/derived relations remain distinct from bounded heuristics.

## Index and budgets

Candidate generation uses a deterministic stdlib uniform grid over finite
bounds. Nodes, cells and candidate pairs are sorted canonically. There is no
unbounded all-pairs fallback.

The lab defaults, each with a server-side cap, are:

| Budget | Default | Hard cap |
|---|---:|---:|
| source entities | 5,000 | 10,000 |
| projected bytes | 8 MiB | 16 MiB |
| spatial cells | 50,000 | 100,000 |
| cells per node | 128 | 256 |
| candidates per node | 256 | 512 |
| relation candidates | 250,000 | 500,000 |
| relations | 100,000 | 200,000 |
| contours | 10,000 | 20,000 |
| features | 25,000 | 50,000 |
| issues | 25,000 | 50,000 |
| build seconds | 10 | 30 |
| scene bytes | 16 MiB | 32 MiB |
| page size | 100 | 200 |

Budget overflow fails `scene_budget_exceeded`. A persisted partial scene is
allowed only when it explicitly has `complete=false`, truncation reasons and
omitted counts. V0 defaults to fail-before-publication.

## Storage, retention and cursor

SQLite remains durable truth. Migration `0012_phase10_scenes.sql` creates:

- `scene_records` for immutable owner/source/root metadata;
- `scene_sections` for one bounded canonical JSON payload per section.

Root and sections insert atomically. Owner+scene lookup is mandatory.
Semantic updates are rejected. Expiry deletion is allowed and cascades to
sections. There is no foreign key to snapshots because a scene may outlive
snapshot TTL; retained evidence must say whether the source snapshot remains
available.

Retention is 24 hours in the explicit lab profile, bounded by policy. Disabling
build/tools never deletes retained audit scenes.

Phase 10 uses a new authenticated cursor. It is URL-safe base64 payload plus
HMAC-SHA256 over:

```text
schema + owner binding + scene ID + section + filter digest
+ offset + projection version
```

The signing secret is at least 32 bytes and required only when public scene
tools/resources are enabled. Verification uses `hmac.compare_digest`. Old
entity cursor behavior is unchanged.

## Drawing text policy

Text, attributes, block names, layer names and metadata are untrusted drawing
content. Scene summaries omit raw text by default. Explicit retrieval is
bounded, authorized and labelled untrusted. No URL/path/command in drawing
content is followed or executed. Logs retain IDs, digests, counts, versions
and safe codes, not raw drawing payload.

## Workflow and Portal

Typed workflow steps may be `build_scene`, `query_scene` and
`validate_scene`; they call an internal port, never a public MCP tool.
Scene child idempotency binds source digest while preserving existing Phase 9
keys for existing actions. Restart state retains scene ID/digest and exact
source snapshot/revision. Existing catalog version `1.0.0` is immutable; scene
integration publishes a new version.

Cleanup stays audit-only. Auto-dimension may consume exact scene evidence but
still prepares a normal Phase 8 program and cannot approve or commit.

Portal adds only owner-scoped GET diagnostics and strict read-only scene pages.
It has no approve-inference, retry-write, editor or scene mutation endpoint.

## Rollout and rollback

All Phase 10 flags default off outside an explicit lab profile. Disable in this
order: public tools/resources, workflow steps, inference packs, new builds.
Retained scenes stay read-only. No destructive DB downgrade is used. Existing
observe/query and Phase 6–9 write/recovery paths remain available.

## Accepted start gates

The integration owner accepts the boundary, public surface, ID/digest,
tolerance, Tier A, storage/retention, redaction, authority and benchmark plans
above. Implementation may proceed. Engineering GO still requires every gate in
`Phase-10.md`, including live R25 evidence; this ADR is not GO evidence.
