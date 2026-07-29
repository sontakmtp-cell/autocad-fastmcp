# Phase 9 security diff review

## Scope and method

- Reviewed scope: the complete Phase 9 implementation diff from `origin/main`,
  including contracts, catalog assets/loader, planners/templates, SQLite
  migration/repository, workflow state/runner, public FastMCP facade, Portal
  views, feature-flag composition, and Phase 6--8 service adapters.
- Threat model: [`phase9-threat-model.md`](phase9-threat-model.md).
- Tests: `python scripts/test-phase9-conformance.py` — **90 passed**. Full
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
| Duplicate/replayed CAD effect | Atomic run/DAG initialization, deterministic step-driven replay/startup healing, missing-wait repair, durable started-control continuation, CAS action claim, durable dispatch-start/child identity, distinct reconciliation envelopes, conflicting-terminal rejection, no reclaim of started write, cancellation of unstarted actions, and `outcome_unknown` recovery through Phase 7 approval/job/receipt plus validate/finish truth. | Covered by failure-injection and repeated reconciliation tests. Live Host and Gateway restart drills retained the exact receipt/checkpoint and stable child identities without duplicate CAD effect. |
| Approval/risk/capability injection | Contract tests reject trusted/execution-shaped extras; the public facade cannot approve, and commit requests use the existing Phase 7 service. The Agent compatibility fix forwards only the exact approved command identity, canonical timestamp and signed capability evidence. | Covered by unit/public-surface tests and scoped public OAuth plus trusted Portal approval. Approval authority did not move into the Agent or Host. |
| Delete/topology/LT/mixed checkpoints | Reference assets use audit-only cleanup and bounded create workflows; the final Agent/Host compatibility changes add no operation or execution capability. | Static asset review and existing Phase 8 conformance. |

## Residual risk and required final checks

Owner-safe `not_found`, the bounded
four-tool surface, rejection of approval/trusted fields, policy/capability
checks, default-off flags, atomic run creation, idempotent control replay,
revocation during continuation, public preview/commit outbox dispatch, and
durable post-approval reconciliation through existing Phase 7
intent/job/receipt state have automated coverage. The retained R25 evidence
proves direct Host receipt/checkpoint recovery after a manual save, AutoCAD
restart and rollback. The public workflow evidence additionally proves durable
action/outbox transport, Gateway restart, Agent reconnect, stable child
identities and no duplicate effect.

Portal unit/E2E tests and the bounded signed-R25 effect-path drills pass.
Scoped public OAuth plus trusted Portal approval is covered; no ChatGPT
Agent-to-AutoCAD end-to-end claim is made. The former live-restart blocker is
cleared and the committed evidence supports **Phase 9 Engineering GO**. All
Phase 9 feature flags remain default-off outside the explicit lab profile, and
Customer Pilot remains **NO-GO**.

The local revision sidecar is availability/recovery evidence under the existing
trusted Windows-user boundary, not a hostile-tamper control. Invalid JSON,
document/fingerprint mismatch or DWG hash mismatch fails closed; sidecar I/O
failure is isolated from AutoCAD Save and simply disables revision restoration.
