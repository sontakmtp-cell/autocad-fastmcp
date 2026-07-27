# Phase 6 Public CAD Program v0 — implementation evidence

> Status: complete for local engineering review on
> `codex/phase-6-public-cad-program-v0`.
>
> Safety boundary: create-only Managed .NET R25, write disabled by default,
> no AutoCAD LT write, no trusted approval, no public rollback, and no
> arbitrary code, path, assembly, or command execution.

## 1. Baseline and environment

- Main baseline: `a3ddacc5e45fa2a3dbf1966ed2d35f12d04a55a7`.
- Phase 6 architecture plan: commits `e6a97f4` through `4ffb1c0`.
- Windows lab: .NET SDK `8.0.423`.
- Live CAD target: AutoCAD Mechanical 2025, full edition, R25.0.
- Authorized test drawing: `D:\Tai lieu\CAD\drawing33.dwg`.
- Final unsigned lab bundle package hash:
  `sha256:17b374bf436831af971d9871e0a5faff8b653e36f4bedb1c575b77490e6da4dd`.

## 2. Requirement-to-evidence matrix

| Requirement | Evidence | Result |
|---|---|---|
| Strict `cad.program/0.2` | JSON schema, golden digest vectors, registry and negative tests | Pass |
| Owner-scoped lifecycle | owner-key repository queries, public tool/resource tests, direct URL cross-owner E2E | Pass |
| Prepare has no CAD effect | Gateway prepare does not dispatch; semantic program excludes runtime authority | Pass |
| Preview has no CAD effect | Host database transaction abort plus live `drawing_unchanged=true` | Pass |
| Atomic create-only commit | Host transaction contains geometry and XRecord receipt; live commit and validation | Pass |
| Exact retry has no second effect | durable receipt lookup and duplicate tests; live duplicate receipt run | Pass |
| Exact binding and stale denial | document/runtime/package/capability/registry/policy checks and tests | Pass |
| Unknown outcome safety | `needs_attention` retains the document write lock; later writes return `document_write_busy` | Pass |
| Write default-off | Gateway/Agent config defaults and launcher regression test | Pass |
| Managed R25 only | runtime broker has no write fallback; LT and high-risk flags are rejected | Pass |
| Portal is observation-only | no approval/mutation controls; owner-safe resources and Gateway-backed release status | Pass |
| Arbitrary execution boundary | strict operation registry; no raw command/path/assembly API | Pass |
| Rollback readiness | explicit flags, package backup, read-only fallback retained | Pass for local lab |

## 3. Automated verification

Final main-agent runs:

| Surface | Result |
|---|---|
| Contracts and Host contracts | 46 passed |
| Gateway | 204 passed |
| Desktop Agent | 104 passed |
| Managed Host core | 43 passed |
| Portal unit/component | 22 passed |
| Portal Playwright | 6 passed |
| Next.js production build | Pass |
| Portal safety validator | Pass |

Important regression coverage includes:

- owner isolation and ID guessing returning `not_found`;
- program/preview/commit/validation exact binding;
- one write per document and durable idempotency;
- stale snapshot, package, capability, registry, and policy denial;
- no LT or compatibility write fallback;
- no blind retry after `outcome_unknown`;
- launcher write gates remaining off unless `-EnableManagedWrite` is explicit;
- Portal failing closed when Gateway release status is unavailable.

## 4. Contract and Gateway evidence

- The program registry contains only:
  `ensure_layer`, `create_line`, `create_circle`, `create_polyline`,
  `create_rectangle`, `create_text`, and `create_dimension_linear`.
- Golden program digest:
  `sha256:11ad7650bc721a2e109d14797d9c7d345d3e698e582ded8b8113594d4a277f60`.
- Operation registry digest:
  `sha256:5dee5cb2d709f06acff2b8678bb084cd9bfa5d1988e9712510c299d61ba30eb8`.
- Public FastMCP tools are `cad_prepare_program`, `cad_preview`,
  `cad_commit`, and `cad_validate`.
- Program, preview, job, receipt, and validation records are scoped by the
  authenticated owner key.
- A commit whose result cannot prove its exact binding becomes
  `outcome_unknown` then `needs_attention`; its document write lock remains
  held until reconciliation.

## 5. Desktop Agent and Managed Host evidence

- `ProgramCommandExecutor` validates payload hash, deadline, R25 binding,
  active document, revision, feature flags, allowlist, and local write lock.
- All write routing goes through `RuntimeBroker`; compatibility and LT
  runtimes cannot be selected for write.
- Python deadlines are normalized to the seven fractional digits required by
  the Managed Host round-trip timestamp parser.
- Preview runs in an aborted AutoCAD database transaction.
- Commit writes geometry and the durable XRecord receipt in one transaction.
- Linear dimensions recompute their dimension block before receipt extents
  are captured.
- A new document incarnation receives a high-entropy numeric revision seed.
  Reopening `drawing33.dwg` changed the live revision from the prior session
  to `7465311242961838`, preventing a revision-`1` stale snapshot collision.

## 6. Live Mechanical 2025 evidence

### Full seven-operation run

On `drawing33.dwg`, the live Host executed one program containing all seven
registered operations:

- preview: 7 operations, 6 entities, 1 layer;
- `transaction_aborted=true`;
- `drawing_unchanged=true`;
- commit: 6 entities, revision `2` to `3`;
- receipt:
  `AUTOCAD_MCP_PROGRAM_25ce185cb5727b0c8389b8bbe750dbc4`;
- validation: passed receipt binding, handles, types, layers, bounds, and
  document revision;
- exact duplicate: same receipt and digest, no second revision change.

This run exposed and fixed the dimension-block extent defect.

### Agent-to-Host run after independent review fixes

After rebuilding, reinstalling, and reopening the DWG with the final bundle:

- path: `ProgramCommandExecutor → RuntimeBroker → Managed Host → AutoCAD`;
- preview: 2 operations, 1 entity, 1 layer, drawing unchanged;
- revision before: `7465311242961838`;
- commit revision after: `7465311242961839`;
- receipt:
  `AUTOCAD_MCP_PROGRAM_fbee37395f0a1eaeb0025decb19d28bd`;
- validation: valid, with all six bounded checks and no failures;
- drawing saved successfully after the run.

## 7. Portal and release-state evidence

- Program, preview, receipt, validation, and job pages render bounded
  owner-scoped summaries.
- Exact runtime/package/capability binding and invalidation reasons are
  visible.
- `outcome_unknown` copy explicitly says there is no automatic write retry.
- Portal has no trusted-approval or write action.
- Portal release display now reads the authenticated Gateway
  `/api/portal/v1/phase6/status` source of truth and fails closed when that
  endpoint is unavailable.
- Failure matrix:
  `apps/web_portal/evidence/phase6-failure-matrix.md`.

## 8. Independent security and reliability review

The independent review initially found:

1. document revision reset after reopen;
2. document lock released on unknown commit;
3. launchers enabling write by default;
4. Portal release state sourced only from local environment.

All four were addressed with focused regression tests. The follow-up review
result is recorded with the final handoff.

No review evidence found LT write, COM write, raw command execution, high-risk
operations, public trusted approval, or owner-boundary bypass.

## 9. GO/NO-GO

### Engineering GO

**GO for draft PR and local integration review.**

The implementation boundary, automated suites, independent review fixes, and
live Mechanical 2025 execution are evidenced.

### Customer Pilot GO

**NO-GO.** The following production prerequisites are intentionally outside
this implementation and remain required:

- CA-issued code-signing certificate and trusted timestamp;
- private-key custody, malware scan, SBOM, and build provenance approval;
- authenticated public Gateway OAuth lifecycle run against the pilot tenant;
- live revoke and re-pair drill;
- modal/busy and unknown-outcome reconciliation drill on the pilot device;
- 3–7 day telemetry soak;
- support ownership and explicit pilot cohort approval.

## 10. Rollback

- Leave `-EnableManagedWrite` absent on both Phase 6 launchers.
- Set program and managed-write flags off; keep LT read-only.
- Preserve audit, program, job, and receipt records for investigation.
- Restore the prior per-user bundle backup:
  `AutocadMcp.ManagedHost.R25.bundle.backup-20260727-181615`.
- Restart Mechanical 2025 and verify the read-only health/observation path.
