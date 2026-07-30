# Phase 10 scene rollback and operator guide

## Default posture

Keep every `AUTOCAD_MCP_PHASE10_*` flag disabled outside the explicit signed
R25 lab profile. Enabling a dependent surface never enables the scene engine
implicitly. Scene tools, resources, mechanical inference, annotation links,
workflow steps and Portal views remain independently gated.

Phase 10 is read-only. A scene, feature, issue, confidence value or inferred
entity reference never authorizes a CAD Program operation, lowers risk, bypasses
approval or changes Phase 6–9 recovery behavior.

## Disable order

For an incident or rollback:

1. Disable public scene tools and scene resources.
2. Disable Portal scene views and workflow scene steps.
3. Disable annotation links and mechanical feature inference.
4. Stop new scene builds by disabling the scene engine.
5. Preserve immutable scene roots, sections, cursor/audit metadata and workflow
   child references for investigation.
6. Reconcile workflows waiting on scene work to `paused` or `needs_attention`
   under the typed workflow policy. Do not translate a scene failure into a CAD
   write retry.

Disabling publication or inference must not delete previously retained scene
records. Do not remove source snapshots, jobs, workflows, intents, consents,
receipts, validations or recovery records. Do not run a destructive database
downgrade.

## Incident handling

- Cross-owner disclosure: disable all public/Portal scene reads, preserve logs
  and database evidence, rotate the cursor signing secret, invalidate issued
  cursors and verify owner-filtered repository reads before re-enabling.
- Vulnerable engine/profile: stop new builds, revoke the exact engine/profile
  version in policy, retain existing scenes as quarantined audit records, then
  publish a new engine version. Never mutate an immutable scene in place.
- Budget exhaustion: keep the scene unpublished or explicitly partial with a
  typed truncation reason. Do not raise limits beyond server caps during an
  incident.
- Prompt-like drawing content: disable affected optional text/annotation
  projection, retain only redacted evidence, and verify there was no network,
  file, command, workflow, risk or approval side effect.
- Started or unknown CAD action: follow the existing Phase 7–9 job,
  reconciliation and recovery path. Phase 10 does not authorize replay.

## Re-enable checklist

Re-enable from the inside out: scene engine, reviewed inference packs, internal
workflow step, resources/tools, then Portal visibility. Before each step verify:

- exact owner/device/snapshot binding and safe `not_found`;
- cursor signature and filter binding;
- immutable restart retrieval;
- bounded counters and explicit completeness;
- default-off dependency validation;
- unchanged Phase 6–9 approval/write/recovery behavior;
- no Critical or High security finding.

Live R25 re-enable additionally requires all three Phase 10 drawings, unchanged
document revision before/after scene work, Gateway restart retrieval and no CAD
effect. As of 2026-07-30 one signed combined drawing proves a partial read-only
run and in-process repository reopen only. The three-drawing outcomes and real
Gateway process restart remain incomplete, so Phase 10 remains Engineering
**NO-GO**.
