# Phase 7 — Durable Recovery, Trusted Approval and Rollback

> Trạng thái: kế hoạch triển khai chi tiết.
>
> Baseline: sau [Phase-6.md](./Phase-6.md) đạt Engineering GO.
>
> Tài liệu nguồn:
>
> - [fastmcp-multi-user-autocad-plan.md](./fastmcp-multi-user-autocad-plan.md)
> - [Phase-6-plus.md](./Phase-6-plus.md)
> - [Phase-6.md](./Phase-6.md)
> - [appendix-user-interface.md](./appendix-user-interface.md)
> - [Phase-3.1.md](./Phase-3.1.md)

## 1. Executive summary

Phase 6 productize create-only CAD Program thành public flow:

```text
prepare → preview → commit → validate
```

Phase 7 làm write path đó đủ an toàn cho customer pilot giới hạn bằng một C2 control envelope:

```text
ChatGPT proposes exact operation
→ Gateway creates immutable execution intent
→ trusted human channel approves exact binding when required
→ Gateway atomically releases one durable job
→ Agent + Host persist ordered execution evidence
→ reconnect proves outcome or escalates needs_attention
→ checkpoint can be previewed and rolled back when conflict-free
```

Không thành phần nào được suy đoán success chỉ vì AutoCAD đang mở, document revision đổi hoặc WSS reconnect. Evidence authoritative phải bind exact command, payload, document, runtime, package và effect.

## 2. Định nghĩa C2

C2 là mức write đầu tiên có:

1. public create-only CAD Program;
2. durable execution Gateway–Agent–Host;
3. trusted approval ngoài model;
4. tested failure/drop matrix;
5. exact-once effect theo receipt evidence;
6. checkpoint và conflict-aware rollback;
7. hard pause, revoke và kill switches xuyên state machine;
8. operator recovery cho `outcome_unknown`.

C2 không có nghĩa là broad modify/delete/pattern, mọi AutoCAD release, production scale hoặc arbitrary code.

## 3. Mục tiêu

1. Không duplicate effect tại mọi tested drop point.
2. Tách admission, approval, execution, effect và outcome.
3. Model/public MCP caller không thể tự approve.
4. Approval bind exact owner/device/document/program/preview/runtime/package/registry/policy/TTL.
5. Một consent release tối đa một execution job.
6. Reconcile theo Host receipt → Agent ledger → Gateway truth.
7. Không auto retry write đã started hoặc chưa chứng minh not-started.
8. Biến `outcome_unknown` thành durable recovery workflow.
9. Cung cấp rollback create-only có preview, approval, conflict report và receipt.
10. Giữ Managed .NET R25 là primary C2 path; LT write vẫn off.

## 4. Dependency gates

### 4.1. Phase 6

- `cad.program/0.2` strict và cross-language digest xanh;
- public prepare/preview/commit/validate owner-scoped;
- preview transaction abort trên Mechanical 2025;
- exact commit một effect;
- durable receipt sống qua restart/reopen;
- runtime/package/capability/registry/policy change invalid preview;
- started write không auto retry;
- hard pause/write lock và independent kill switches.

### 4.2. Identity

- owner filtering phủ intent, consent, checkpoint và recovery case;
- live revoke/re-pair xanh;
- replacement session vô hiệu old result;
- Agent local confirmation gắn đúng paired device;
- Portal approval dùng authenticated session và recent-auth khi policy yêu cầu.

### 4.3. Managed Host

- receipt/checkpoint ghi cùng transaction với effect khi khả thi;
- bounded recovery query không thực thi lại operation;
- Host unload/reload không mất DWG receipt;
- AutoCAD crash/reopen fixture có operator evidence.

## 5. Non-goals

- broad modify/delete/purge/move/copy/pattern/block editing;
- arbitrary code/command/path/plugin;
- organization/team ownership;
- billing;
- skill/workflow orchestration;
- Scene Graph;
- full installer/update ecosystem;
- multi-worker/PostgreSQL migration;
- semantic conflict auto-merge;
- generic AutoCAD Undo as rollback guarantee;
- silent runtime fallback;
- manual mark-success to hide unknown outcome.

## 6. Execution intent

Approval không gắn trực tiếp vào queued job. Gateway tạo immutable `ExecutionIntentRecord` trước dispatch.

Flow:

```text
commit request
→ create immutable intent + consent if required
→ wait for trusted action
→ revalidate exact binding
→ atomically consume consent and create queued job
→ dispatch through durable state machine
```

Lợi ích:

- không thêm `awaiting_approval` vào job state machine;
- deny/expiry không tạo Agent command;
- queued write không stale khi chờ user;
- retry của model không release job thứ hai;
- một intent có audit timeline rõ.

Intent bind:

- owner/user actor;
- device/session;
- document identity/revision;
- program/preview/execution digest;
- runtime/Host/Agent/package;
- capability/registry/policy;
- risk class/assurance required;
- expiry;
- intended effect summary generated from trusted program fields.

## 7. Consent model

Approval không phải MCP tool. Không tạo `cad_approve`, `confirm=true` hoặc equivalent.

Trusted presenters:

- Desktop Agent UI trên paired device;
- Web Portal/companion page với authenticated browser session.

Cùng đọc/ghi một Gateway `ConsentRecord`.

States:

```text
requested
approved
released
consumed
denied
expired
invalidated
```

`approved` chỉ là authorization decision. `released/consumed` phải CAS/transaction cùng việc tạo hoặc claim exact execution job.

### Assurance levels

- `device_local_confirmation`: local operator, dùng cho medium-risk create-only khi policy cho phép;
- `user_recent_auth`: Auth0 recent-auth, bắt buộc cho high-risk, rollback lớn, revoke-sensitive và future destructive operations.

Final assurance là mức hạn chế nhất của operation floor, tenant policy, device policy và risk class.

## 8. Evidence model

Authoritative order:

1. Managed Host durable receipt bound exact command/execution digest trong DWG;
2. Agent durable ledger bound Host evidence;
3. Gateway terminal result committed atomically với artifacts/records.

Không dùng đơn lẻ:

- document revision changed;
- entity count changed;
- WSS reconnect;
- AutoCAD process alive;
- UI spinner completed.

### Host execution milestones

Tối thiểu:

```text
host_admitted
transaction_started
effect_committed
receipt_committed
result_materialized
```

Agent ledger tối thiểu:

```text
received
accepted
host_dispatched
host_started
host_terminal
gateway_result_persisted
```

Milestones phải monotonic, payload-hash bound và bounded.

## 9. Drop matrix

Test các điểm:

1. trước Gateway dispatch;
2. sau dispatch trước Agent receive;
3. sau Agent receive trước ACK;
4. sau ACK trước Host dispatch;
5. sau Host admit trước transaction;
6. trong transaction trước effect;
7. sau effect trước receipt;
8. sau receipt trước Host result;
9. sau Host result trước Agent terminal persist;
10. sau Agent persist trước Gateway result;
11. sau Gateway result trước client response.

Expected semantics:

- proven `not_started` có thể requeue theo existing state machine;
- any `started` evidence không auto retry;
- receipt proves success even if result lost;
- no receipt + ambiguous started becomes `outcome_unknown`/`needs_attention`;
- exact client retry returns existing intent/job/result where binding matches.

## 10. Reconcile protocol

Gateway reconcile descriptor bổ sung effect/execution digest.

Agent recovery query:

- inspect local ledger;
- query Host receipt by exact command/execution digest;
- never re-execute during reconcile;
- return bounded evidence and source.

Host recovery query:

- read durable receipt/checkpoint only;
- validate document identity;
- return `not_found`, `committed`, `rolled_back` or conflict-safe status;
- no mutation.

Reconcile outcome:

| Evidence | Gateway action |
|---|---|
| Proven not-started | Requeue if deadline/policy permits |
| Receipt committed | Materialize success idempotently |
| Proven transaction aborted/no effect | Fail/cancel safely |
| Started but inconclusive | `outcome_unknown` |
| Evidence mismatch/conflict | `needs_attention` |

## 11. Outcome unknown workflow

Create owner-scoped `RecoveryCaseRecord` with:

- immutable execution binding;
- ordered Gateway/Agent/Host timeline;
- evidence sources and missing evidence;
- current document/runtime/package state;
- safe actions;
- operator notes/audit;
- resolution state.

Safe actions may include:

- retry evidence query;
- ask user to reopen exact DWG;
- collect redacted diagnostics;
- confirm receipt manually from trusted Host query;
- mark unresolved/needs-support.

Prohibited:

- re-run original write;
- manually mark success without receipt;
- generic undo;
- delete history to clear alert.

## 12. Checkpoint and rollback

Phase 6 checkpoint is internal evidence. Phase 7 productizes it.

### Rollback lifecycle

```text
checkpoint inspect
→ rollback preview
→ conflict report
→ trusted approval if required
→ rollback commit
→ rollback receipt
→ validate
```

Rollback binding:

- original commit receipt;
- exact document identity;
- expected current revision;
- created entity refs/handles/fingerprints;
- runtime/package/registry;
- rollback plan digest;
- policy/approval.

### Conflict detection

Fail closed when:

- created entities changed/deleted;
- dependent entities reference them;
- document switched/reopened without stable identity;
- runtime/registry cannot execute exact rollback;
- revision diverged beyond accepted scope;
- checkpoint/receipt mismatch.

Do not promise generic Undo. Rollback must be explicit inverse/create-only cleanup based on receipt evidence.

## 13. Public surface delta

Potential public tools/resources:

- `cad_request_commit` may return `approval_required` plus intent resource;
- `cad_get_job` includes recovery-safe state/evidence summary;
- `cad_prepare_rollback`;
- `cad_commit_rollback`;
- existing resource URIs extended for intents, consents, checkpoints and recovery cases.

No public approval tool.

Resources:

```text
cad://intents/{intent_id}
cad://consents/{consent_id}
cad://checkpoints/{checkpoint_id}
cad://rollbacks/{rollback_id}
cad://recovery/{case_id}
```

## 14. Storage

Additive migration records:

- `execution_intents`;
- `consents`;
- `execution_evidence_events`;
- `checkpoints`;
- `rollback_plans`;
- `rollback_receipts`;
- `recovery_cases`.

Transactions/CAS:

- consent state transition;
- intent release;
- job creation;
- terminal result/evidence materialization;
- recovery resolution.

All owner-scoped and audit-correlated.

## 15. UI

### Agent

- exact operation/program summary from trusted fields;
- document/runtime/package/risk;
- approve/deny local when assurance permits;
- hard pause/write lock;
- execution milestones;
- outcome unknown warning;
- diagnostics/support ID.

### Portal

- recent-auth approval;
- immutable intent details;
- approve/deny/expired/invalidated;
- activity timeline;
- rollback preview/conflict;
- recovery case view.

UI must not render model-supplied free text as trusted operation facts.

## 16. Security

- CSRF/origin and recent-auth for Portal mutations;
- local Agent action bound device/session;
- one-time consent consume;
- replay and race tests;
- owner IDOR on all new records;
- revoke invalidates pending intents/consents where policy requires;
- runtime/package/document change invalidates approval;
- no token/private key/pipe secret in UI/logs;
- no arbitrary rollback payload.

## 17. Feature flags

```text
AUTOCAD_MCP_TRUSTED_APPROVAL_ENABLED=0
AUTOCAD_MCP_DEVICE_LOCAL_APPROVAL_ENABLED=0
AUTOCAD_MCP_PORTAL_RECENT_AUTH_APPROVAL_ENABLED=0
AUTOCAD_MCP_PUBLIC_ROLLBACK_ENABLED=0
AUTOCAD_MCP_RECOVERY_CASES_ENABLED=0
```

Separate kill switches for commit, approval presenter and rollback.

## 18. Test matrix

### Domain/storage

- immutable intent;
- consent expiry/deny/invalidation;
- approval consume race;
- one consent → one job;
- owner isolation;
- atomic terminal evidence;
- recovery resolution CAS.

### Protocol/recovery

- all drop points;
- Agent/Host/AutoCAD/Gateway restart;
- Host unload/reload;
- DWG close/reopen;
- WSS replacement session;
- exact/conflicting duplicate;
- receipt/result loss combinations.

### Approval

- model cannot approve;
- local vs recent-auth assurance;
- CSRF/session mismatch;
- runtime/package/document change invalidation;
- expiry/replay;
- revoke while pending/approved.

### Rollback

- clean rollback;
- entity modified/deleted/dependency conflict;
- stale revision;
- duplicate rollback;
- result lost after rollback receipt;
- rollback validate.

### Regression

- Phase 0–6 read/write contracts;
- identity/pairing isolation;
- LT read compatibility;
- telemetry fail-open;
- package rollback.

## 19. Live acceptance

On Mechanical 2025:

1. Prepare/preview a create-only program.
2. Commit requires trusted approval under test policy.
3. Model retry cannot approve or create second job.
4. Cut WSS/Agent/pipe/Host at each planned boundary.
5. Drawing changes at most once.
6. Receipt-based reconcile materializes correct outcome.
7. Ambiguous case becomes outcome unknown, no retry.
8. Reopen DWG and recover receipt.
9. Prepare rollback, show no-conflict preview, approve and commit.
10. Duplicate rollback does not repeat effect.
11. Modify a created entity and verify rollback conflict.
12. Revoke device during pending consent and verify invalidation/connection close.

## 20. GO/NO-GO

GO when:

- drop matrix has no duplicate effect;
- exact outcome proved or escalated safely;
- model cannot approve;
- consent consume/release atomic;
- rollback conflict-aware and idempotent;
- hard pause/revoke/kill switches work across states;
- owner isolation and redaction green;
- live Mechanical 2025 evidence complete.

NO-GO when any started write can be auto-retried, success is inferred without receipt, approval can be replayed/self-issued, rollback uses generic Undo without conflict proof, or recovery history can be overwritten.

## 21. Rollback of Phase 7 rollout

- disable trusted approval presenters and public rollback;
- retain Phase 6 low-risk lab path only if policy permits;
- keep intents/consents/recovery records for audit;
- do not delete unknown cases;
- return production profile to read-only if recovery semantics regress;
- keep LT unchanged.

## 22. Definition of Done

Phase 7 hoàn tất khi create-only public write chịu được tested network/process failures without duplicate effect, trusted human approval cannot be forged by the model, ambiguous outcomes enter a safe recovery workflow, and exact checkpoint rollback succeeds only when conflict-free.
