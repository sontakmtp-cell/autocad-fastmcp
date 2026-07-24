# Phase 5 Managed Runtime Foundation evidence

Date: 2026-07-25
Branch: `phase-5`

R25 signing and rollback details:
`phase5-signing-clean-vm-evidence.md`.

## Scope and dependency gate

Phase 5 was integrated in the documented order. Entity/revision and CAD Program
work started only after the Mechanical 2025 read-only path completed:

`ChatGPT/Codex MCP → Gateway → Agent → cad.host/1 Named Pipe → Managed .NET Host`

The read-only gate returned `runtime.id=managed_dotnet`, role `primary`,
Host family `R25`, framework `.NET 8`, and an exact package hash. The existing
SafeFileIPC/AutoLISP path remained available and its regression suite stayed
green. No arbitrary command, LISP, assembly, shell, network listener, or
unbounded path operation was added to `cad.host/1`.

## Phase results

| Phase | Implemented evidence | Exit status |
|---|---|---|
| 5.0 contracts/runtime seam | ADR, additive Agent contracts, capability manifest/hash, `RuntimeBroker`, explicit fail-closed flags, old simulator parsing | Pass |
| 5.1 Managed read-only Host | .NET 8 R25 solution, current-user authenticated Named Pipe, health/product/document/summary/layers, Agent adapter, local lab bundle | Pass on Mechanical 2025 |
| 5.2 entity/revision | Bounded entity page with type/layer/handle/space/bounds, event summary/cursor, instance identity, database/event revision, stale/busy/modal/document-switch errors | Pass in code tests and real entity page |
| 5.3 CAD Program v0 | Exact create-only registry for layer/line/circle/polyline/text, validate, transaction preview/abort, commit/checkpoint, durable DWG receipt and duplicate reconcile | Pass on Mechanical 2025 POC |
| 5.4 release families | R22–R25 manifest plus R25 Authenticode builder, signed installer, receipt, upgrade and rollback | R25 engineering pass; old families deferred |
| 5.5 LT compatibility | Legacy code untouched, LT route/capability/no-Managed-Host automated regression and limitation matrix | Deferred by operator; not certified |
| 5.6 isolation | Owner-scoped runtime/Host evidence tests and existing Gateway owner filters | Partial; live two-user/two-device/revoke lab missing |
| 5.7 runtime policy | Runtime/release diagnostics, safe telemetry field policy and separate fail-closed kill switches | Scaffold; pilot/signing/telemetry ingestion missing |

## Fresh Mechanical 2025 evidence

### Managed read-only public path

Final public observation:

- job: `job-57803a6d-2be3-4dfd-823c-efeb3e5e733f`
- snapshot: `snapshot-command-e8549531-1db1-4c59-ab03-96c8cfa617ff`
- document: `phase5-write-lab.dwg`
- entity count: `4`
- layers: `0`, `MCP-PHASE5-LAB`
- runtime: `managed_dotnet`, role `primary`, family `R25`, `.NET 8`
- package: `autocad.managed_host.r25` `0.1.0`
- package hash:
  `sha256:34d22623dfa4121550486ef6057d4eb4fb6b8c22ee70fea401c62758333815f1`

The job event order was `queued → dispatched → acknowledged → succeeded`.
Gateway owner-scoped job materialization retained the runtime evidence.

### Entity and CAD Program POC

The real lab started with an empty `Drawing1.dwg`:

- entity page before: `0`, revision `2`
- validate: `validated`
- preview: `previewed`, database transaction aborted
- preview document before/after: `2 / 2`
- entity page after preview: `0`, revision `2`
- commit: `committed`
- created: one layer and four entities
- resulting types: `LINE`, `CIRCLE`, `POLYLINE`, `TEXT`
- checkpoint: `checkpoint-8558537b2a752e468f3fe7f7`
- immediate duplicate: `duplicate`, `effect_applied=false`

The drawing was saved only as the isolated test artifact
`tmp/phase5-write-lab.dwg`, AutoCAD/Host was restarted, and the file was
reopened. The document instance ID changed from
`doc-db2bc1c521df6f8f5ba1e313` to
`doc-9250b46f6123b79def802547`, while the DWG-bound receipt survived. Replaying
the exact old commit returned:

- status: `duplicate`
- durable receipt: `true`
- duplicate of succeeded commit: `true`
- effect applied: `false`
- entity count before/after reconcile: `4 / 4`

This closes the observed Agent disconnect and Host/AutoCAD restart duplicate
case for the POC. The receipt key is an opaque hash, receipt size and count are
bounded, and entity effects plus receipt commit atomically in one database
transaction.

During the lab, entity timestamps exposed a cross-language canonical JSON
escaping mismatch. The Host canonical writer now uses relaxed UTF-8 escaping
to match the shared Python contract; a fixed cross-language hash regression
test covers `+` timestamps and Vietnamese text.

## Automated validation

- repository suite: `396 passed, 1 skipped`
- Gateway: `180 passed`
- Desktop Agent: `49 passed`
- shared contracts: `5 passed`
- Managed Host Core: `24 passed`
- Host contract/golden messages: `3 passed`
- packaging + LT focused tests: `12 passed`
- R25 build/publish: `0 warnings, 0 errors`
- release validator: pass
- `git diff --check`: pass

The skipped repository test and the Python deprecation warnings are unrelated
to Phase 5 behavior.

## Definition of Done review

| Architecture DoD item | Current evidence |
|---|---|
| Public Managed .NET observation on real Mechanical 2025 | Pass |
| Same Agent/Gateway contract retains LT/File IPC compatibility | Pass by automated regression and feature-flag routing |
| Runtime/role/release/package/capability evidence | Pass |
| Preview/commit runtime and execution binding | Pass |
| Create-only program through direct .NET API, no generated LISP | Pass on real lab |
| Duplicate does not reapply after disconnect/restart/reopen | Pass for the real POC plus automated cases |
| No arbitrary code/path/assembly/public Host listener | Pass by registry/protocol/package review |
| At least one pre-2025 build family tested | Deferred by operator; not certified |
| Real AutoCAD LT 2024+ regression | Deferred by operator; not certified |
| Signed installer and clean-VM rollback | Partial: self-signed Authenticode R25 and isolated exact-hash rollback pass; CA certificate/timestamp and authorized VM execution pending |
| Live two-user/two-device/revoke isolation | Blocked: second identity/device lab unavailable |
| Runtime telemetry pilot and support exercise | Blocked: production pilot unavailable |

Phase 5 implementation and the R25 runtime POC are complete in this branch, but
the architecture-wide production DoD remains **partial**. Release policy
therefore keeps `managed_write=false`, `lt_write=false`, `high_risk=false`,
`advanced_lisp=false`, and `arbitrary_code=false`. The branch must not be
labelled production-certified until the remaining CA/timestamp, authorized
clean-VM, multi-user and pilot evidence exists. Deferred family/LT items remain
explicitly uncertified.
