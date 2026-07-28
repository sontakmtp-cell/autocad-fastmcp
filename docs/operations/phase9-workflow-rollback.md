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
