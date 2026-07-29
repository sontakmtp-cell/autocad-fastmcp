# Phase 9 workflow rollback and operator guide

## Default posture

Keep every `AUTOCAD_MCP_PHASE9_*` flag disabled outside an explicitly approved
lab profile. Catalog read may be enabled before the engine; read-only workflow
execution may be enabled before any write workflow. A flag never overrides
catalog status, owner/device access, policy epoch, capability evidence, Phase 7
approval, or Phase 8 sealing.

## Incident response

1. Disable public workflow tools, then the workflow engine.
2. Stop claiming pending workflow actions; preserve runs, events, waits, and
   action records for audit.
3. Keep Phase 6--8 freeform CAD Program tools available under their existing
   controls.
4. For a bad skill, security-revoke its exact version and move the default
   channel to a prior published version. Do not edit an immutable version.
5. For a started or `outcome_unknown` CAD action, use existing job/recovery and
   rollback procedures before marking the workflow terminal. Do not retry the
   write from the workflow.
6. Retain Portal diagnostics read-only. Do not delete intent, job, receipt,
   recovery, checkpoint, or SQLite rows, and do not perform a destructive DB
   downgrade.

## Live acceptance checklist

Record commit, operator/date, device/runtime/Host/package evidence, catalog and
policy epochs, exact skill/workflow/planner/template digests, fixture drawing,
commands, retained artifacts, failures, and retests. Verify: auto-dimension
with explicit LINE/LWPOLYLINE targets; plate pattern with bounded variables and
repeat; cleanup audit with unchanged drawing; preview before approval; receipt,
validation, and eligible rollback; restart/reconnect; and no duplicate effect
on replay. A headless result is not live R25 acceptance.

## Evidence recorded on 2026-07-29

- [`phase9-live-r25-effect-path-20260729.json`](../architecture/evidence/phase9-live-r25-effect-path-20260729.json)
  records signed lab R25 preview/commit/validate/rollback drills for
  auto-dimension (`46 -> 48 -> 46`) and plate creation
  (`46 -> 55 -> 46`), plus a cleanup audit with an unchanged document
  revision.
- [`phase9-live-r25-host-restart-20260729.json`](../architecture/evidence/phase9-live-r25-host-restart-20260729.json)
  records a manual Save/close/reopen cycle that restored revision
  `7348076429262431`, receipt
  `AUTOCAD_MCP_PHASE8_337b9e601d42747fdd96f770da61505c` and checkpoint
  `AUTOCAD_MCP_PHASE8_CP_5ab618f311da37915addc729cb7d60c1`; rollback
  validation passed, the entity count returned from `48` to `46`, and every
  baseline entity handle/fingerprint pair was restored.

These artifacts prove the existing Host effect/recovery path only. A live
Phase 9 Gateway workflow across Gateway restart, Agent reconnect and replay
with stable action/job/intent identities is still required. Cleanup report
retrieval across a Gateway restart and public OAuth/ChatGPT operation also
remain unverified. Keep all Phase 9 flags default-off.
