# Phase 9 security diff review

## Scope and method

- Reviewed scope: the complete Phase 9 implementation diff from `origin/main`,
  including contracts, catalog assets/loader, planners/templates, SQLite
  migration/repository, workflow state/runner, public FastMCP facade, Portal
  views, feature-flag composition, and Phase 6--8 service adapters.
- Threat model: [`phase9-threat-model.md`](phase9-threat-model.md).
- Tests: `python scripts/test-phase9-conformance.py` — **68 passed**. Full
  regression and Phase 8 conformance results are recorded in
  [`Phase-9.md`](../architecture/Phase-9.md).

## Review result

No reportable critical or high security finding was identified in the reviewed
Phase 9 diff.
The reviewed implementation has direct controls for the material new attack
families:

| Risk | Evidence in the diff | Result |
| --- | --- | --- |
| Arbitrary code/path/URL/plugin/tool execution | Strict contracts reject execution-shaped fields and forbidden step kinds; pure planner module has no I/O imports. | Covered by unit tests; no execution sink found. |
| Catalog tampering/default drift | Fixed-root asset digest verification, release digest verification, immutable version/definition records, and run pins. | Covered by unit and public-resource tests. |
| Cross-owner workflow access | Run mutation/read methods include `owner_subject` lookup and cross-owner test returns the safe `not_found` conflict. | Simulated repository coverage only. |
| Duplicate/replayed CAD effect | Atomic run/DAG initialization, deterministic replay/startup healing, CAS action claim, durable dispatch-start/child identity, distinct reconciliation envelopes, conflicting-terminal rejection, no reclaim of started write, cancellation of unstarted actions, and `outcome_unknown` recovery into Phase 7 approval/job/receipt truth. | Covered by failure-injection, real-port restart, repeated reconciliation, cancellation, and duplicate-completion tests. |
| Approval/risk/capability injection | Contract tests reject trusted/execution-shaped extras; the public facade cannot approve, and commit requests use the existing Phase 7 service. | Covered by unit/public-surface tests; live evidence pending. |
| Delete/topology/LT/mixed checkpoints | Reference assets use audit-only cleanup and bounded create workflows; no new Agent/Host contract. | Static asset review only. |

## Residual risk and required final checks

This is not a final Phase 9 Engineering GO. Owner-safe `not_found`, the bounded
four-tool surface, rejection of approval/trusted fields, policy/capability
checks, default-off flags, atomic run creation, idempotent control replay,
revocation during continuation, public preview/commit outbox dispatch, and
durable post-approval reconciliation through existing Phase 7
intent/job/receipt state have automated coverage. Restart/reconnect and
no-duplicate-effect acceptance still require live R25 evidence.

Portal unit/E2E tests pass, but no live AutoCAD or public OAuth/ChatGPT claim is
made here. Until the required live evidence exists, all Phase 9 feature flags
remain default-off and the status is **NO-GO for Engineering GO / Customer
Pilot**.
