# Phase 9 security diff review

## Scope and method

- Reviewed diff: `origin/main...eb890ec8224ba68310ff7191baa0f9065434a949`.
- Discovery worklist: 22 changed paths: contracts, catalog assets/loader,
  pure planners/templates, SQLite migration/repository, workflow state/runner,
  package glue, and the Phase 8 conformance allowance.
- Threat model: [`phase9-threat-model.md`](phase9-threat-model.md).
- Tests: `python scripts/test-phase9-conformance.py` — 35 passed; one existing
  unregistered `pytest.mark.phase3` warning. This warning is not a security
  finding.

## Review result

No reportable critical or high finding was identified in this Wave 1 diff.
The reviewed implementation has direct controls for the material new attack
families:

| Risk | Evidence in the diff | Result |
| --- | --- | --- |
| Arbitrary code/path/URL/plugin/tool execution | Strict contracts reject execution-shaped fields and forbidden step kinds; pure planner module has no I/O imports. | Covered by unit tests; no execution sink found. |
| Catalog tampering/default drift | Fixed-root asset digest verification, release digest verification, immutable version/definition records, and run pins. | Covered by unit tests; publication UI/API still pending. |
| Cross-owner workflow access | Run mutation/read methods include `owner_subject` lookup and cross-owner test returns the safe `not_found` conflict. | Simulated repository coverage only. |
| Duplicate/replayed CAD effect | CAS action claim, durable dispatch-start record, deterministic keys, no reclaim of started write, and `outcome_unknown` to recovery. | Simulated repository/runner coverage only. |
| Approval/risk/capability injection | Contract tests reject trusted/execution-shaped extras; no workflow approval step exists in the Wave 1 runner. | Public facade enforcement pending. |
| Delete/topology/LT/mixed checkpoints | Reference assets use audit-only cleanup and bounded create workflows; no new Agent/Host contract. | Static asset review only. |

## Residual risk and required final checks

This is not a final Phase 9 Engineering GO. The public FastMCP facade, Portal
routes, feature-flag composition, actual Phase 7 approval adapter, and live
R25 writes were outside this Wave 1 diff or not yet integrated. Final review
must re-run after those changes and prove: owner-safe `not_found` for all
public tools/resources; browser cannot submit approval/trusted fields; no
skill-specific tool registration; actual policy/capability/revocation checks;
and restart/reconnect against the real job/recovery services.

No live AutoCAD, public OAuth/ChatGPT, or Portal E2E claim is made here. Until
that evidence exists, all Phase 9 feature flags remain default-off and the
status is **NO-GO for Engineering GO / Customer Pilot**.
