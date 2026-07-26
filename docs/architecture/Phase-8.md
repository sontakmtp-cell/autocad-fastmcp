# Phase 8 — Durable Execution Recovery, Trusted Approval and C2

> Trạng thái: kế hoạch triển khai chi tiết.
>
> Baseline: nhánh `phase-5`, sau Phase 5 Managed Runtime Foundation; việc mở cho người dùng phụ thuộc Phase 6 identity/pairing và Phase 7 public trusted write path đạt exit gate.
>
> Tài liệu nguồn:
>
> - [fastmcp-multi-user-autocad-plan.md](./fastmcp-multi-user-autocad-plan.md)
> - [Phase-6-plus.md](./Phase-6-plus.md)
> - [Phase-7.md](./Phase-7.md)
> - [appendix-user-interface.md](./appendix-user-interface.md)
> - [Phase-3.1.md](./Phase-3.1.md)
> - [phase5-runtime-foundation-evidence.md](./phase5-runtime-foundation-evidence.md)
>
> Phase 8 không mở rộng broad CAD capability. Mục tiêu của nó là làm cho write path create-only của Phase 7 có thể chịu mất mạng/crash, nhận phê duyệt từ một kênh người thật đáng tin cậy, rollback có kiểm tra xung đột và có quy trình xử lý `outcome_unknown` trên AutoCAD thật.

## 1. Executive summary

Phase 5 đã chứng minh trên AutoCAD Mechanical 2025 thật rằng Managed .NET Host có thể preview bằng transaction abort, commit trực tiếp, tạo checkpoint và giữ durable DWG receipt qua Agent/Host/AutoCAD restart cùng DWG reopen.

Phase 7 được thiết kế để productize POC đó thành public owner-scoped flow:

```text
prepare → preview → commit → validate
```

Tuy nhiên Phase 7 chủ động giữ recovery ở mức conservative:

- write đã `started` không được tự retry;
- exact duplicate có thể trả prior receipt;
- trường hợp chưa chứng minh được outcome chuyển `outcome_unknown` hoặc `needs_attention`;
- checkpoint chỉ là evidence, chưa phải public rollback guarantee;
- trusted approval chưa hoàn chỉnh.

Phase 8 đóng các khoảng trống đó bằng một C2 write envelope:

```text
ChatGPT proposes exact operation
→ Gateway creates immutable execution intent
→ trusted human channel approves exact binding when policy requires
→ Gateway atomically releases one durable job
→ Agent + Host persist ordered execution evidence
→ reconnect proves succeeded/failed/cancelled or escalates needs_attention
→ exact checkpoint can be previewed and rolled back when conflict-free
```

Không thành phần nào được suy đoán success chỉ vì AutoCAD đang mở, revision đã đổi hoặc WSS đã reconnect. Evidence phải đi từ receipt/ledger đã bind exact command, payload, document, runtime và package.

## 2. Định nghĩa C2 trong roadmap này

C2 là mức write đầu tiên có đầy đủ control envelope cho customer pilot giới hạn:

1. public CAD Program create-only đã được runtime-pin;
2. durable execution qua Gateway, Agent và Managed Host;
3. trusted confirmation/approval ngoài model;
4. failure/drop matrix có reconcile thực;
5. exact-once effect theo evidence, không phải theo retry may mắn;
6. checkpoint và rollback có conflict report;
7. hard pause, revoke và kill switch hoạt động xuyên suốt state machine;
8. operator có thể chẩn đoán unknown outcome mà không sửa lịch sử hoặc chạy lại mù.

C2 không có nghĩa là:

- hỗ trợ modify/delete/pattern/block tổng quát;
- hỗ trợ mọi AutoCAD version/vertical;
- production scale hoặc multi-worker;
- marketplace/skill ecosystem;
- arbitrary AutoLISP, C#, DLL, shell, command hoặc file/network access.

Broad CAD Program v1 thuộc Phase 9. Customer packaging/pilot hoàn chỉnh thuộc Phase 12. Production SLO/scale thuộc Phase 13.

## 3. Baseline assessment trên nhánh `phase-5`

### 3.1. Phần đã có và phải tái sử dụng

- Gateway có durable SQLite job truth, owner filters và state machine từ Phase 3.1.
- Các state `reconnect_pending`, `outcome_unknown` và `needs_attention` đã tồn tại.
- Chỉ reconcile evidence `not_started` mới được requeue; `started` không được tự retry.
- Agent có SQLite `CommandLedger`, sequence bền, cancel intent, hard pause và terminal persist-before-send.
- Agent có `RuntimeBroker`, Managed .NET adapter và package/runtime evidence.
- `cad.host/1` có authenticated Named Pipe, bounded frame, payload hash, sequence và deadline.
- Managed Host R25 có transaction preview/commit, DWG receipt và checkpoint POC.
- Receipt đã chứng minh sống qua Agent/Host/AutoCAD restart và DWG reopen.
- UI Agent đã có runtime/product/package status, hard pause và cảnh báo `outcome_unknown` ở mức nền.
- Phase 7 đã định nghĩa public program/preview/commit/validate records, exact execution binding và low-risk write path cần productize.

### 3.2. Khoảng trống Phase 8 phải đóng

1. Agent ledger hiện chỉ có state coarse `received/accepted/started/terminal`; chưa có evidence rõ về transaction/effect/result durability.
2. Managed Host receipt chưa được dùng như một recovery protocol đầy đủ cho mọi drop point.
3. Gateway chưa có immutable execution intent và consent record một lần.
4. Model/public MCP caller chưa bị tách hoàn toàn khỏi trusted approval action.
5. Chưa có assurance level khác nhau giữa local Agent confirmation và Portal recent-auth approval.
6. Approval chưa có atomic consume/release semantics.
7. Checkpoint chưa có owner-scoped public lifecycle, rollback preview, conflict report hoặc rollback receipt.
8. `outcome_unknown` chưa có durable recovery case, operator timeline và safe resolution workflow.
9. Drop matrix chưa chạy xuyên Gateway → WSS → Agent → Named Pipe → AutoCAD transaction thật.
10. AutoCAD close/crash, Host unload/reload và process restart chưa được kiểm ở từng execution boundary.
11. Portal/Agent chưa cùng render một approval record từ một nguồn sự thật.
12. LT/File IPC chưa có evidence đủ mạnh cho C2 write; mặc định phải fail closed.

## 4. Mục tiêu Phase 8

1. Bảo đảm không có duplicate effect tại mọi drop point đã định nghĩa.
2. Phân biệt rõ admission, approval, execution, effect và outcome.
3. Chỉ trusted human channel được approve; model không thể tự xác nhận.
4. Bind approval vào exact user, device, document, program, preview, runtime, package, registry, policy và TTL.
5. Consume approval đúng một lần và release tối đa một durable execution job.
6. Reconcile dựa trên evidence theo thứ tự Host receipt → Agent ledger → Gateway durable result.
7. Không auto retry write đã có evidence `started` hoặc không chứng minh được `not_started`.
8. Biến `outcome_unknown` thành một recovery workflow có timeline, evidence và action an toàn.
9. Cung cấp rollback create-only có preview, approval, conflict detection và idempotent receipt.
10. Giữ AutoCAD Full Managed .NET là C2 primary path.
11. Giữ AutoCAD LT read/compatibility không regression; LT C2 write chỉ mở sau certification riêng.
12. Giữ public read tools, Phase 0–7 contracts, owner isolation và local profile không regression.

## 5. Dependency gates

### 5.1. Phase 6 identity/pairing gate

Trước mọi customer C2 write, Phase 6 phải chứng minh:

- Auth0 `(issuer, sub)` map đúng internal user;
- production device pairing one-time code không replay;
- device credential/key được bảo vệ và rotate/revoke được;
- owner filtering phủ device, session, program, preview, intent, consent, checkpoint, recovery case, job, result và resource;
- revoke đóng active WSS và chặn reconnect;
- replacement session vô hiệu session cũ;
- two-user/two-device isolation chạy thật.

Agent local confirmation chỉ được coi là hành động của operator trên đúng paired device. Nó không thay thế user recent-auth cho action có assurance cao.

### 5.2. Phase 7 write-path gate

Phase 7 phải đạt tối thiểu:

- `cad.program/0.2` strict và cross-language digest xanh;
- public `cad_prepare_program`, `cad_preview`, `cad_commit`, `cad_validate` owner-scoped;
- preview transaction abort trên Mechanical 2025 thật;
- exact commit tạo effect một lần;
- durable receipt sống qua restart/reopen;
- runtime/package/capability/registry/policy change invalid preview;
- unknown started write không tự retry;
- hard pause/local write lock chặn trước Host;
- `managed_write` và `lt_write` có kill switch độc lập.

Phase 8 có thể phát triển trên fixture/lab trước khi Phase 7 hoàn tất, nhưng không được giả định schema hoặc semantics chưa freeze.

### 5.3. Managed .NET gate

- R25 package hash/signature hợp lệ trong cohort lab.
- Host handshake, document identity và strong revision evidence hợp lệ.
- Receipt/checkpoint ghi cùng AutoCAD transaction với effect.
- Host có bounded recovery query không thực thi lại operation.
- Host unload/reload không làm mất DWG-bound receipt.
- AutoCAD crash/reopen fixture có evidence được operator xác nhận.

### 5.4. UI trust gate

- Agent action đi qua Agent Core, không gọi Host/COM/File IPC trực tiếp.
- Portal action đi qua authenticated Portal API, không gọi MCP tool hoặc Agent WSS như device.
- Approval dialog render dữ liệu từ immutable execution intent/evidence, không render mô tả do model tự khai như trusted fields.
- High-assurance approval có recent-auth và CSRF protection.

## 6. Non-goals

Phase 8 không làm:

- modify/delete/purge/move/copy/rotate/pattern/block editing tổng quát;
- arbitrary code, command, path hoặc plugin execution;
- organization/team shared ownership;
- billing/subscription;
- skill/workflow orchestration;
- Scene Graph hoặc DWG viewer;
- installer/update ecosystem hoàn chỉnh;
- multi-worker dispatcher, Redis hoặc PostgreSQL migration;
- automatic semantic conflict merge;
- rollback dựa trên generic AutoCAD Undo stack;
- silent runtime fallback;
- mark-success thủ công để che `outcome_unknown`.

## 7. Quyết định thiết kế khóa

### 7.1. Approval không phải MCP tool

Không tạo public tool kiểu `cad_approve`, `confirm=true` hoặc field để model tự approve.

Trusted approval chỉ đến từ:

- Desktop Agent UI trên đúng paired device; hoặc
- Web Portal/companion page với authenticated browser session.

ChatGPT chỉ nhận trạng thái `approval_required`, `approval_pending`, `approved/released`, `denied`, `expired` hoặc `invalidated` và resource/link phù hợp.

### 7.2. Execution intent tách khỏi durable job

Khi policy yêu cầu approval, Gateway tạo `ExecutionIntentRecord` immutable nhưng chưa dispatch Agent và chưa tạo effect-bearing job.

Flow:

```text
commit request
→ create immutable intent + consent
→ wait for trusted action
→ revalidate binding
→ atomically consume consent and create queued job
→ dispatch through normal durable state machine
```

Lợi ích:

- không thêm `awaiting_approval` vào job state machine hiện có;
- không giữ queued write stale trong khi chờ user;
- deny/expiry không tạo Agent command;
- một approval chỉ release tối đa một job;
- model retry không thể tạo job thứ hai nếu intent đã được release.

### 7.3. Một consent record, nhiều trusted presenter

Agent và Portal cùng đọc/ghi một Gateway `ConsentRecord`. Không có hai approval truth riêng.

Consent state tối thiểu:

```text
requested
approved
released
consumed
denied
expired
invalidated
```

`approved` chỉ là authorization decision. `released/consumed` phải xảy ra bằng transaction/CAS cùng việc tạo hoặc claim exact execution job.

### 7.4. Assurance level

Hai assurance level tối thiểu:

- `device_local_confirmation`: Agent local action; dùng cho medium-risk create-only trong đúng paired device.
- `user_recent_auth`: Portal action sau Auth0 recent-auth; bắt buộc cho high-risk policy, rollback lớn, revoke-sensitive hoặc future destructive operations.

Policy lấy mức hạn chế nhất giữa risk class, tenant policy, device policy và operation floor.

### 7.5. Evidence không suy đoán

Thứ tự evidence authoritative:

1. Managed Host durable receipt bound exact command/execution digest trong DWG;
2. Agent durable terminal result bound exact Host receipt/hash;
3. Gateway atomic terminal result/materialization.

Revision change, entity count hoặc process presence chỉ là supporting evidence. Chúng không tự biến unknown thành success.

### 7.6. Rollback là operation mới, không phải Undo

Rollback:

- có own preview/digest/idempotency key;
- cần capability và policy riêng;
- chạy trong transaction mới;
- tạo rollback receipt mới;
- không gọi generic `UNDO`, gửi `ESC` hoặc sửa command stack;
- fail closed khi revision/conflict không chứng minh được an toàn.

## 8. State model và invariants

### 8.1. Gateway job states giữ nguyên

Phase 8 giữ state machine đã harden:

```text
queued
→ dispatched
→ acknowledged
→ running
→ succeeded | failed | cancelled

non-terminal disconnect
→ reconnect_pending
→ running | queued | outcome_unknown | terminal

contradictory/insufficient evidence
→ needs_attention
```

Không thêm shortcut từ `outcome_unknown` về `queued`.

### 8.2. Execution intent states

```text
proposed
→ awaiting_approval
→ approved
→ released
→ consumed

awaiting_approval → denied | expired | invalidated
approved → expired | invalidated khi binding đổi trước release
```

Invariant:

- intent immutable về owner/device/document/program/preview/execution digest;
- một intent link tối đa một consent;
- một consent release tối đa một job;
- release dùng unique constraint/CAS;
- denied/expired/invalidated không thể revive;
- exact model retry trả prior intent/job, không tạo intent mới;
- key reuse với payload khác trả `idempotency_conflict`.

### 8.3. Agent ledger mở rộng additive

Giữ state coarse để backward compatibility, bổ sung evidence fields/versioned schema:

- `attempt_id`;
- `execution_phase`;
- `host_session_id`;
- `host_command_id`;
- `host_payload_hash`;
- `transaction_state`;
- `effect_state`;
- `host_receipt_id/hash`;
- `checkpoint_id/hash`;
- `document_before_revision`;
- `document_after_revision`;
- `terminal_result_hash`;
- `terminal_persisted_at`;
- `last_recovery_probe_at`;
- `recovery_probe_result`.

`execution_phase` tối thiểu:

```text
before_host
host_accepted
host_started
transaction_open
transaction_committed
effect_receipted
terminal_persisted
```

Field này là evidence, không thay Gateway job state.

### 8.4. Managed Host receipt states

Receipt record phải phân biệt:

- operation chưa tồn tại;
- accepted nhưng chưa bắt đầu;
- transaction bắt đầu nhưng chưa commit;
- effect committed + receipt committed;
- terminal no-effect failure;
- rollback committed;
- conflicting replay.

Host recovery query tuyệt đối không thực thi operation. Nó chỉ đọc bounded receipt/evidence.

## 9. Execution evidence contract

Mọi write/rollback result và recovery response bind tối thiểu:

- owner-scoped device ID ở Gateway/Agent boundary;
- job ID, command ID, attempt ID;
- action-specific idempotency key;
- payload hash;
- program ID/revision/digest;
- preview or rollback-preview ID/digest;
- execution digest;
- document ID/instance ID;
- before/expected/after revision;
- runtime ID/role/version;
- Host family/version;
- package ID/version/SHA-256;
- capability manifest hash;
- operation registry version/hash;
- policy version;
- Host receipt ID/hash;
- checkpoint ID/hash khi commit tạo effect;
- timestamps và monotonic sequence;
- effect status `none|committed|rolled_back|unknown`;
- validation summary bounded.

Canonical hashing phải có Python/C# golden vectors cho Unicode, timestamp, floating point, null/optional fields và field ordering.

## 10. Failure/drop matrix

### 10.1. Bắt buộc bao phủ

| Drop point | Evidence mong đợi | Recovery policy |
|---|---|---|
| Trước Gateway dispatch | Không có Agent ledger | Job vẫn queued hoặc safe failure; không effect |
| Sau dispatch, trước Agent ACK | Agent `not_started` hoặc không có entry | Reconcile; chỉ `not_started` mới redispatch same command |
| Sau Agent persist `accepted`, trước `started` | Agent entry accepted, Host không có receipt | Có thể chứng minh not-started; redispatch exact command qua CAS |
| Sau Agent persist `started`, trước Host accept | Agent started, Host không có record | Không retry tự động; probe bounded rồi `outcome_unknown/needs_attention` nếu không chứng minh |
| Sau Host accept, trước transaction | Host accepted/not-started evidence | Reconcile exact command; chỉ execute khi Host chứng minh no effect và same command |
| Trong transaction, trước commit | Không có committed receipt | Sau Host/AutoCAD recovery phải chứng minh transaction abort/no effect; nếu không đủ evidence → needs_attention |
| Sau effect commit + receipt, trước Host response | DWG receipt authoritative | Reconstruct succeeded result; không reapply |
| Sau Host response, trước Agent terminal persist | Host receipt/result hash | Agent reconstruct terminal rồi persist trước send |
| Sau Agent terminal persist, trước WSS result | Agent terminal authoritative | Reconnect gửi exact terminal evidence |
| Sau WSS result, trước Gateway finalization | Agent/Host terminal evidence | Gateway atomic finalize hoặc accept identical duplicate |
| Sau Gateway terminalization | Full terminal record | Duplicate no-op; conflict rejected/audited |
| AutoCAD close bình thường giữa các bước | Bounded Host/Agent evidence | Chờ/reconcile; không gửi command mù khi document không active |
| AutoCAD crash | Receipt + DWG reopen evidence | Prove terminal/no-effect hoặc needs_attention |
| Host unload/reload | Host receipt survives in DWG | Re-handshake, query receipt, do not reapply |
| Agent restart | Local SQLite ledger survives | Reconnect/reconcile exact command |
| Gateway restart | SQLite truth survives | Recover non-terminal jobs/intents/consents safely |
| Revoke/session replacement | Old session rejected | No old-session terminal/result accepted |

### 10.2. Quy tắc kết luận

- `not_started` phải là evidence exact command/payload, không phải “không thấy process”.
- `started` không được auto retry.
- `transaction_open` sau crash chỉ thành no-effect khi Host/AutoCAD evidence chứng minh abort và không có receipt.
- committed receipt thắng timeout/disconnect.
- contradictory receipt, revision hoặc payload chuyển `needs_attention`.
- `needs_attention` không được tự đổi thành success/failed do timer.

## 11. Trusted approval design

### 11.1. Consent binding

Consent bind exact:

- internal user/owner;
- device;
- document identity và expected revision;
- program ID/revision/digest;
- preview ID/digest;
- execution digest;
- runtime ID/role/version;
- Host/compiler/package version/hash;
- capability manifest hash;
- registry version/hash;
- risk policy version;
- operation summary/count/bounds;
- assurance level;
- created/expires timestamps;
- one-time nonce/version.

Thay đổi bất kỳ binding nào làm consent invalid.

### 11.2. Agent confirmation

Agent UI:

- nhận consent notification qua Agent Core/WSS typed message;
- render basename drawing, operation summary, object counts, runtime, package, expiry và preview link/summary;
- nút `Từ chối` và `Xác nhận một lần`;
- action ký/bind bằng active device session và consent nonce;
- không giữ approval offline để gửi sau expiry;
- không approve nếu Agent đang paused, revoked, runtime changed hoặc document mismatch;
- không expose raw payload, full path, secret hoặc generated code.

Medium-risk local confirmation có thể dùng flow này.

### 11.3. Portal approval

Portal:

- authenticated browser session;
- CSRF protection;
- recent-auth cho assurance cao;
- owner-check consent/intention/resource;
- render cùng immutable summary từ Gateway;
- approve/deny exact record;
- không gửi owner ID từ browser rồi tin nó;
- không gọi Agent WSS, Named Pipe, COM hoặc public MCP tool.

### 11.4. Release transaction

Approval release transaction phải:

1. lock/CAS intent + consent;
2. kiểm requested/approved, chưa expired/invalidated/consumed;
3. kiểm owner/device/policy binding;
4. revalidate latest durable device/runtime/package/capability evidence;
5. tạo exact durable job + command identity;
6. link job vào intent/consent;
7. mark released/consumed;
8. append audit event;
9. commit một lần.

Nếu bước nào fail, không tạo partial job hoặc consumed consent.

## 12. Risk policy trong Phase 8

Tối thiểu:

| Risk | Phase 8 behavior |
|---|---|
| Low create-only | Direct commit hoặc preview + commit theo tenant mode; vẫn cần local write lock |
| Medium create-only/bounded annotation | Trusted `device_local_confirmation` hoặc stronger |
| High-risk fixture/future destructive | `user_recent_auth`; global high-risk flag mặc định OFF |
| Rollback | Ít nhất medium; high khi effect count/bounds vượt threshold hoặc policy yêu cầu |
| Unknown outcome manual action | Không có “approve retry”; chỉ evidence probe, acknowledge hoặc support escalation |

Risk floor do operation registry/policy quyết định. UI/model không được hạ.

## 13. Rollback model cho create-only C2

### 13.1. Checkpoint record

Commit checkpoint phải lưu bounded evidence:

- checkpoint ID/hash;
- originating job/command/receipt;
- owner/device/document;
- before/after revision;
- program/preview/execution digest;
- exact created entity handles + type/layer summary;
- layers created bởi operation và trạng thái tồn tại trước đó;
- package/runtime/registry binding;
- rollback capability/version;
- created_at, expiry/retention policy;
- validation hash.

Checkpoint không chứa full DWG hoặc arbitrary serialized AutoCAD object.

### 13.2. `cad_prepare_rollback`

Input:

- `checkpoint_id`;
- optional request idempotency key.

Hành vi:

- scope `autocad.write`;
- owner/device/document check;
- originating commit phải succeeded và chưa rollback;
- query live document/revision qua durable read/recovery path;
- verify exact created handles still exist and match bounded fingerprints;
- detect layer/entity conflicts;
- create immutable rollback preview/digest;
- không mutate drawing.

Output:

- rollback preview ID/digest;
- objects/layers dự kiến xóa;
- current vs expected revision;
- conflict report;
- required assurance/risk;
- expiry;
- `ready_for_approval` hoặc fail reason.

### 13.3. `cad_rollback`

Input:

- exact rollback preview ID;
- idempotency key.

Hành vi:

- require trusted approval theo policy;
- no silent runtime fallback;
- default C2 yêu cầu exact current revision bằng revision đã dùng khi tạo rollback preview;
- Host transaction mới xóa đúng entity do originating receipt tạo;
- layer chỉ xóa nếu checkpoint chứng minh layer do commit tạo và hiện rỗng/không conflict;
- ghi rollback receipt trong cùng transaction;
- duplicate trả prior rollback result;
- conflicting duplicate reject;
- validation chứng minh target effect đã được đảo ngược trong scope cho phép.

### 13.4. Conflict policy

Phase 8 mặc định fail closed khi:

- document revision đổi sau rollback preview;
- created handle bị erase/replaced/modified;
- layer có entity mới ngoài checkpoint;
- runtime/package/registry đổi;
- originating receipt/checkpoint thiếu hoặc hash mismatch;
- drawing instance/path identity không chứng minh được;
- rollback receipt contradictory.

Phase 8 chỉ trả conflict report; không tự merge, không generic Undo và không xóa gần đúng. Non-overlap semantic rollback có thể xem xét ở Phase 9+.

## 14. Public MCP và resource delta

### 14.1. Existing tool behavior

`cad_commit` Phase 8:

- nếu không cần approval: giữ direct durable job flow;
- nếu cần approval: tạo/reuse immutable intent + consent và trả `approval_required`;
- không dispatch Agent trước approval;
- sau trusted approval, Gateway release exact job; ChatGPT có thể poll bằng job/intent resource;
- exact retry không tạo job thứ hai.

`cad_get_job` mở rộng bounded fields:

- approval/intent state;
- recovery state/case ID;
- effect status;
- receipt/checkpoint summary;
- rollback availability;
- safe next action.

### 14.2. New public tools

Tối thiểu:

- `cad_prepare_rollback`
- `cad_rollback`

Không thêm public approve/force-retry/mark-success tool.

### 14.3. Owner-scoped resources

- `cad://execution-intent/{intent_id}`
- `cad://consent/{consent_id}`
- `cad://checkpoint/{checkpoint_id}`
- `cad://rollback-preview/{rollback_preview_id}`
- `cad://recovery/{recovery_case_id}`
- existing `cad://job/{job_id}` với C2 evidence.

Resource phải bounded, redact secret/session/path/drawing content và fail closed `not_found` cho cross-owner lookup.

## 15. Gateway design

### 15.1. Domain records

Thêm:

- `ExecutionIntentRecord`;
- `ConsentRecord`;
- `ConsentDecision`;
- `CheckpointRecord`;
- `RollbackPreviewRecord`;
- `RecoveryCaseRecord`;
- `RecoveryEvidenceRecord`;
- `OperatorActionRecord`;
- `ExecutionAttemptRecord` nếu không gộp vào job events.

### 15.2. SQLite migrations

Dùng additive numbered migration sau migration cuối của Phase 7; không đoán/sửa migration cũ.

Tables/indexes tối thiểu:

- execution intents + owner/action/idempotency unique identity;
- consents + nonce/version/state/expiry/assurance;
- consent decisions/audit;
- intent-to-job unique release link;
- checkpoints;
- rollback previews;
- recovery cases/evidence/actions;
- indexes owner/device/document/status/expiry/job/receipt;
- partial/unique constraints ngăn double consume/double release;
- foreign keys tới owner-scoped program/preview/job/device khi repository model cho phép.

Atomic operations:

- create/reuse intent + consent;
- approve/deny CAS;
- consume + job creation;
- terminal result + checkpoint materialization;
- recovery evidence + state transition;
- rollback result + rollback receipt/checkpoint state.

### 15.3. Application services

Tách khỏi `app.py`:

- `ExecutionIntentService`;
- `ConsentService`;
- `ApprovalPolicyService`;
- `ExecutionReleaseService`;
- `RecoveryService`;
- `CheckpointService`;
- `RollbackService`;
- `C2Materializer`;
- `OperatorRecoveryService`.

FastMCP facade chỉ parse typed input, resolve principal/correlation ID và map safe errors.

Portal API gọi cùng application services, không có business rules riêng.

### 15.4. Scheduler/reconcile

Maintenance loop bổ sung:

- expire pending consents/intents;
- invalidate stale binding khi durable device/package/policy evidence đổi;
- reconcile non-terminal write bằng exact Agent/Host evidence;
- create/update recovery case khi unknown vượt bounded probe window;
- không tự terminalize needs-attention;
- không auto retry started write;
- readiness fail nếu C2 maintenance fatal.

## 16. Shared contracts và protocol

### 16.1. `cad.agent/1`

Chỉ giữ version nếu extension additive thật sự backward-compatible. Nếu approval/recovery message bắt buộc làm Agent cũ hiểu sai, tăng protocol version hoặc capability-gate explicit.

Typed messages tối thiểu:

- consent notification/status;
- execution release metadata;
- recovery probe request/response;
- Host receipt/checkpoint evidence;
- rollback preview/execute command;
- hard pause/write-lock state;
- recovery case notification.

Không dùng generic action string do model cung cấp.

### 16.2. `cad.host/1`

Thêm exact operation IDs:

- query execution receipt;
- query checkpoint;
- prepare rollback evidence;
- execute rollback;
- query rollback receipt;
- bounded recovery diagnostics.

Mỗi request vẫn bind handshake/session, command, payload hash, document, deadline và sequence.

### 16.3. Capability manifest

Capabilities ví dụ:

```text
execution.recovery.query
execution.receipt.durable
approval.device_local
program.v0.checkpoint
program.v0.rollback.preview
program.v0.rollback.commit
program.v0.rollback.validate
```

Manifest phải công bố capability thực, không suy từ package name.

## 17. Desktop Agent design

### 17.1. Ledger migration

`CommandLedger` dùng versioned local migration thay vì chỉ `CREATE TABLE IF NOT EXISTS` không kiểm schema.

Bổ sung:

- execution attempts/evidence;
- Host receipt/checkpoint hashes;
- consent notification cache không chứa secret;
- recovery probes;
- rollback receipts;
- migration version/checksum;
- corruption fail closed và diagnostic code.

Terminal result phải persist trước transmission. Recovery evidence cũng phải persist trước gửi Gateway.

### 17.2. Command admission

Trước ACK:

- active non-revoked session;
- exact device;
- command/payload hash;
- intent/consent release evidence khi required;
- local write lock enabled;
- hard pause off;
- runtime/package/registry/capability match;
- document/revision match;
- one active write per device/document;
- deadline/budget valid.

Agent không tự approve và không nhận `confirm=true` trong command payload.

### 17.3. Recovery coordinator

Agent Core có bounded recovery coordinator:

1. load non-terminal ledger entries sau restart;
2. reconnect Gateway;
3. re-handshake Host/runtime;
4. query exact Host receipt by command/payload/execution digest;
5. persist evidence;
6. trả `not_started|started|terminal|contradictory`;
7. không gọi execute trong recovery query;
8. chỉ normal dispatch path sau Gateway reconcile `not_started` mới chạy command.

### 17.4. Hard pause, cancel và revoke

- hard pause chặn admission ngay local và sống qua restart;
- pending approval không tự release khi paused;
- revoke invalidates active WSS và local Host command admission;
- cancel trước start có thể terminalize cancelled;
- cancel sau transaction start chỉ là durable intent, Host dừng ở safe boundary nếu có;
- không kill AutoCAD, gửi ESC hoặc abort transaction mù;
- terminal result có thể thắng cancel race.

## 18. Managed Host design

### 18.1. Receipt ledger

DWG-bound receipt phải:

- key bằng opaque hash của command/action/execution digest;
- bounded count/size;
- atomic cùng transaction effect;
- chứa no secret/full payload;
- phân biệt commit và rollback receipt;
- detect conflicting replay;
- survive save/reopen;
- có deterministic evidence hash cross-language.

### 18.2. Transaction stages

Host scheduler phải map rõ:

```text
accepted
→ document_locked
→ transaction_open
→ effects_applied
→ receipt_written
→ transaction_committed
→ result_materialized
```

Không báo `effect_committed` trước khi AutoCAD transaction và receipt cùng commit thành công.

### 18.3. Recovery query

Recovery query:

- chạy trên AutoCAD-safe context;
- read-only/bounded;
- không tạo entity hoặc receipt mới;
- xác thực exact document/command/payload/execution digest;
- trả receipt/transaction evidence đã tồn tại;
- contradictory evidence trả structured error;
- không suy success từ revision đơn lẻ.

### 18.4. Rollback

- document lock + exact revision;
- validate checkpoint/handles/fingerprints;
- one new transaction;
- erase exact created entities;
- conditionally remove created empty layers;
- write rollback receipt atomic;
- commit once;
- bounded validation;
- duplicate exact rollback returns prior receipt;
- no generic Undo/command string/generated LISP.

## 19. UI/UX tối thiểu

### 19.1. ChatGPT Web

ChatGPT response phải nói rõ:

- operation đang chờ approval hay đã release;
- device/document/runtime;
- approval hết hạn khi nào;
- không có effect trước approval;
- job/recovery status;
- `outcome_unknown` không đồng nghĩa failed;
- rollback availability/conflict summary.

ChatGPT không tự nói “đã được người dùng xác nhận” nếu Gateway chưa có trusted consent record.

### 19.2. Desktop Agent

Bổ sung UI:

- pending consent list/count;
- exact approval dialog;
- current write execution phase ở ngôn ngữ người dùng;
- hard pause/write lock;
- unknown outcome banner;
- recovery timeline;
- `Kiểm tra lại bằng chứng` bounded action;
- rollback preview summary;
- support code và diagnostics export đã redaction.

Không có nút `Chạy lại` cho unknown write.

### 19.3. Portal

Bổ sung:

- activity/approval page;
- pending/approved/denied/expired/invalidated states;
- recent-auth high-assurance flow;
- job/receipt/checkpoint/recovery timeline;
- rollback preview và conflict report;
- owner-scoped filters;
- audit actor/channel/assurance.

Portal không có raw command console hoặc force-success action.

### 19.4. User copy

| State | Copy chính |
|---|---|
| `approval_required` | “Cần bạn xác nhận đúng preview trước khi chạy.” |
| `approval_expired` | “Phê duyệt đã hết hạn. Hãy tạo preview mới.” |
| `approval_invalidated` | “Bản vẽ hoặc môi trường thực thi đã thay đổi. Phê duyệt cũ không còn hiệu lực.” |
| `outcome_unknown` | “Chưa thể xác định thao tác đã hoàn tất hay chưa. Hệ thống sẽ không tự chạy lại.” |
| `needs_attention` | “Cần kiểm tra bản vẽ và bằng chứng trước khi tiếp tục.” |
| `rollback_conflict` | “Không thể rollback an toàn vì bản vẽ đã thay đổi hoặc đối tượng không còn khớp.” |
| `rollback_available` | “Có thể tạo bản xem trước để hoàn tác đúng thay đổi này.” |

## 20. Operator recovery workflow

### 20.1. Recovery case

Khi automatic bounded reconcile không đủ evidence, Gateway tạo `RecoveryCaseRecord`:

- owner/device/document/job/command;
- last known state;
- exact binding hashes;
- Agent/Host/session/package versions;
- ordered evidence timeline;
- contradictions/missing evidence;
- safe next actions;
- support code;
- acknowledgement/escalation status.

### 20.2. Allowed operator actions

- request bounded evidence refresh;
- download redacted diagnostics;
- acknowledge case;
- attach external operator note/evidence reference;
- disable write on device/cohort;
- revoke device;
- escalate support;
- prepare rollback only khi checkpoint/outcome đã proven succeeded.

### 20.3. Forbidden operator actions

- force redispatch started write;
- edit payload/digest binding;
- mark succeeded without receipt/evidence;
- overwrite terminal job history;
- delete audit to “clear” case;
- send raw command/LISP/C#/DLL/path;
- generic Undo remote.

Manual acknowledgement có thể đóng support workflow nhưng không rewrite underlying technical job outcome.

## 21. AutoCAD LT compatibility

Phase 8 Managed .NET exit không tự mở LT C2 write.

Mặc định LT:

- read/observe regression luôn chạy;
- `lt_write=false` và `lt_c2=false` nếu chưa real-certified;
- không load Managed Host;
- không silent fallback một Managed .NET write sang File IPC/LISP;
- unknown started LT write không retry và không gửi ESC;
- rollback capability không công bố nếu chưa có exact packaged inverse + real evidence.

LT C2 chỉ bật khi có:

- AutoCAD LT 2024+ real drop matrix;
- durable dispatcher/receipt semantics;
- exact portable operation registry;
- revision/checkpoint strategy rõ;
- duplicate/restart/reopen evidence;
- rollback conflict behavior;
- separate kill switch và limitation matrix.

Không đạt gate này không chặn Managed .NET Phase 8 exit; UI phải nói rõ LT vẫn read-only hoặc limited write.

## 22. Security controls

1. `autocad.write` bắt buộc cho commit/rollback preparation and execution.
2. Approval action không nằm ở public MCP boundary.
3. Owner filter ở mọi intent/consent/checkpoint/recovery lookup.
4. Recent-auth cho high-assurance Portal action.
5. CSRF cho Portal mutations.
6. Agent local approval bind active device session + nonce/version.
7. Consent one-time consume bằng CAS/unique constraint.
8. Runtime/package/capability/registry/policy revalidate trước release và trước execution.
9. No silent runtime fallback.
10. No write retry after started without exact not-started evidence.
11. Receipt/checkpoint hashes canonical và bounded.
12. No arbitrary code/path/assembly/command/network.
13. Public errors allowlist + correlation ID.
14. Diagnostics redact token, device key, pipe secret, full path, full program và drawing content.
15. Approval notification không chứa sensitive drawing content.
16. Recovery APIs rate/budget limited.
17. Operator action có audit actor/channel/reason.
18. Global/runtime/cohort/high-risk/rollback kill switches fail closed.
19. Old/revoked session result bị reject.
20. Portal/Admin không gửi execution command trực tiếp tới Agent/Host.

## 23. Feature flags và rollout

Tối thiểu:

```text
AUTOCAD_MCP_C2_ENABLED=0|1
AUTOCAD_MCP_APPROVAL_ENABLED=0|1
AUTOCAD_MCP_AGENT_LOCAL_CONFIRMATION_ENABLED=0|1
AUTOCAD_MCP_PORTAL_APPROVAL_ENABLED=0|1
AUTOCAD_MCP_RECOVERY_RECONCILE_ENABLED=0|1
AUTOCAD_MCP_ROLLBACK_ENABLED=0|1
AUTOCAD_MCP_MANAGED_C2_ENABLED=0|1
AUTOCAD_MCP_LT_C2_ENABLED=0|1
AUTOCAD_MCP_HIGH_RISK_ENABLED=0|1
```

Effective policy luôn lấy giá trị hạn chế nhất với Phase 7 write flags. Không để flag cũ/mới mâu thuẫn tạo fail-open.

Rollout order:

1. contract/domain tests only;
2. Host Core receipt/rollback tests;
3. local Agent + fake Host failure matrix;
4. Mechanical 2025 isolated DWG lab;
5. Agent approval UI lab;
6. Portal approval lab;
7. Phase 6 two-user/two-device isolation;
8. internal canary create-only;
9. customer pilot chỉ sau Phase 12 packaging/signing/support gates.

## 24. Implementation work packages

### 8.0 — Contract freeze, threat model và failure taxonomy

- freeze intent/consent/checkpoint/recovery schemas;
- define assurance/risk matrix;
- define evidence hierarchy;
- define exact drop points and expected outcomes;
- threat model approval spoofing, replay, local privilege, receipt tamper và rollback abuse;
- cross-language canonical vectors.

**Exit:** Python/C# hash/parse giống nhau; state/invariant review không còn blocker.

### 8.1 — Gateway intent và consent persistence

- additive migrations;
- owner-scoped repositories;
- create/reuse immutable intent;
- consent state/CAS/expiry/invalidation;
- audit/resources;
- idempotency/IDOR tests.

**Exit:** approval-required commit không tạo Agent command; exact retry reuse intent an toàn.

### 8.2 — Trusted Agent confirmation

- typed consent notification;
- Agent Core intent handling;
- UI dialog/tray badge;
- device-session-bound approve/deny;
- paused/revoked/runtime-changed rejection;
- accessibility/redaction tests.

**Exit:** medium-risk consent được approve/deny từ Agent, model không thể giả mạo.

### 8.3 — Portal approval và atomic release

- Portal API endpoints;
- owner session + CSRF;
- recent-auth assurance;
- approve/deny UI;
- consume + job creation transaction;
- double-click/race/expiry tests.

**Exit:** một consent release tối đa một durable job qua cả Agent và Portal race.

### 8.4 — Agent/Host recovery evidence

- ledger migration/evidence fields;
- Host receipt query;
- recovery coordinator;
- reconnect protocol extension;
- contradictory evidence handling;
- process restart tests.

**Exit:** every named drop point resolves terminal/no-effect or needs-attention without duplicate.

### 8.5 — Checkpoint và rollback preview

- owner-scoped checkpoint materialization;
- checkpoint resources;
- live preflight;
- handle/layer fingerprint validation;
- rollback preview/digest/conflict report;
- policy/risk classification.

**Exit:** prepare rollback mutates nothing and reports exact safe/conflict state.

### 8.6 — Public rollback execution

- `cad_prepare_rollback` and `cad_rollback`;
- approval integration;
- Host transaction/rollback receipt;
- idempotency/reconnect;
- post-rollback validation;
- real DWG demo.

**Exit:** exact rollback runs once; conflict never falls back to generic Undo.

### 8.7 — Operator recovery và diagnostics

- recovery case persistence;
- timeline/evidence UI;
- bounded refresh;
- redacted support bundle;
- acknowledge/escalate/kill-switch actions;
- no force-success/retry controls.

**Exit:** unknown case can be investigated safely without history mutation.

### 8.8 — Full drop matrix, security, regression và evidence

- automated fault injection;
- real AutoCAD crash/close/reopen matrix;
- two-user isolation;
- hosted CI/core tests;
- controlled Windows R25 build/smoke;
- evidence report and GO/NO-GO review.

**Exit:** Definition of Done đạt và không có NO-GO blocker.

## 25. File-level change plan

### Shared contracts

- `packages/contracts/src/autocad_contracts/agent_protocol.py`
- `packages/contracts/src/autocad_contracts/runtime.py`
- new consent/execution/recovery/checkpoint models and canonicalization
- protocol/schema/golden/fuzz tests

### Host contracts

- `packages/host_contracts/schemas/` intent-independent Host recovery/rollback payloads
- golden recovery query/receipt/checkpoint/rollback envelopes
- invalid corpus and cross-language digest vectors

### Gateway

- `services/gateway/src/autocad_gateway/app.py`
- `services/gateway/src/autocad_gateway/contracts.py`
- `services/gateway/src/autocad_gateway/application/` intent, consent, release, recovery, rollback services
- `services/gateway/src/autocad_gateway/domain/` records/invariants
- `services/gateway/src/autocad_gateway/infrastructure/sqlite/` additive migrations/repositories
- durable job/reconcile/materializer extensions
- Portal API/BFF boundary when Phase 6 Portal exists
- owner isolation, race, expiry, recovery and public contract tests

### Desktop Agent

- `apps/desktop_agent/src/autocad_desktop_agent/ledger.py`
- `core.py`
- `state.py`
- `network.py` or current WSS transport module
- `runtime/contracts.py`
- `runtime/broker.py`
- `runtime/managed_dotnet.py`
- typed write/recovery executor
- `ui/window.py` plus focused approval/recovery widgets
- Agent ledger, reconnect, consent, UI and diagnostics tests

### Managed Host

- `native/autocad_managed_host/src/AutocadMcp.Host.Core/Protocol.cs`
- `CadProgram.cs`
- receipt/checkpoint contract modules
- `AutocadMcp.Host.R25/DrawingProgramLedger.cs`
- `AutoCadProgramOperations.cs`
- new recovery/rollback operations
- Host Core tests and real AutoCAD scripts/evidence

### Web Portal

- Phase 6 Portal app/routes/components
- consent/activity/recovery/checkpoint pages
- authenticated Portal API client
- CSRF/recent-auth middleware
- Playwright owner-isolation/approval/race tests

### Documentation

- update `Phase-6-plus.md` status/link when implementation starts;
- update `appendix-user-interface.md` only for implemented behavior;
- add Phase 8 evidence, runbook, failure matrix, rollback limitation and GO/NO-GO report.

## 26. Test matrix

### 26.1. Contract/schema

- valid/invalid intent/consent/checkpoint/recovery payloads;
- unknown fields and version negotiation;
- Unicode/timestamp/floating canonical hash Python/C#;
- consent binding invalidation;
- duplicate/replay/conflicting payload;
- oversized/deep JSON;
- assurance/risk floor.

### 26.2. Gateway domain/repository

- owner isolation across all new records;
- intent immutability;
- consent approve/deny/expire/invalidate;
- Agent vs Portal approval race;
- double-click/double consume;
- approve vs expiry race;
- approve vs runtime/package/document change;
- consume + job creation atomicity;
- restart/migration checksum/backup restore;
- terminal result + checkpoint atomicity;
- recovery evidence transitions;
- needs-attention immutability.

### 26.3. Public MCP contract

- no public approve tool;
- `cad_commit` approval-required response shape;
- `cad_prepare_rollback` and `cad_rollback` schemas/annotations;
- `autocad.write` required;
- safe errors/resources;
- old read and Phase 7 tools unchanged outside approved additive delta;
- flags hide/disable C2 write fail closed.

### 26.4. Agent ledger/recovery

- local migration and corruption;
- accepted/started/effect/terminal evidence order;
- terminal persist-before-send;
- restart at every phase;
- Host receipt reconstruction;
- contradictory Host evidence;
- exact duplicate and conflict;
- hard pause/write lock/revoke/session replacement;
- consent notification expiry and stale click;
- no UI direct runtime call;
- diagnostics redaction.

### 26.5. Managed Host Core

- receipt/checkpoint canonicalization;
- replay/conflict;
- bounded ledger;
- recovery query no side effect;
- transaction stage mapping;
- rollback handle/layer validation;
- rollback receipt idempotency;
- no arbitrary operation/path/assembly/command.

### 26.6. AutoCAD Mechanical 2025 real drop matrix

- drop before Agent ACK;
- drop after ACK before start;
- drop after Agent started before Host accept;
- drop after Host accept before transaction;
- drop during transaction before commit;
- drop after commit/receipt before Host response;
- drop after Host response before Agent persist;
- drop after Agent persist before Gateway result;
- Gateway restart before/after finalization;
- Agent restart at each stage;
- Host unload/reload;
- AutoCAD close;
- AutoCAD crash and DWG reopen;
- document switch/modal/busy;
- package/runtime change;
- revoke/session replacement;
- exact duplicate after every reconnect point.

### 26.7. Approval UI

- Agent medium confirmation approve/deny/expiry;
- Portal recent-auth approval;
- Agent/Portal simultaneous action;
- runtime/document/package change while dialog open;
- paused/revoked device;
- keyboard/focus/DPI/multi-monitor;
- notification privacy;
- no approval from model payload;
- no stale approval reuse.

### 26.8. Rollback

- prepare mutates nothing;
- exact revision success;
- revision changed conflict;
- entity missing/modified conflict;
- layer gained external entity conflict;
- exact duplicate rollback;
- conflicting duplicate;
- drop at every rollback stage;
- rollback receipt after restart/reopen;
- post-rollback validation;
- no generic Undo invocation.

### 26.9. Security

- IDOR across intent/consent/checkpoint/recovery/rollback;
- forged approval actor/channel/assurance;
- CSRF/recent-auth bypass;
- nonce/version replay;
- session/device swap;
- forged receipt/checkpoint/hash;
- payload/path/command/LISP/C#/DLL injection corpus;
- diagnostics/log/UI redaction;
- kill-switch fail-open attempts;
- Portal/Admin direct execution attempts rejected.

### 26.10. LT regression

- current read path always green;
- no Managed Host load;
- no C2 capability by default;
- approval cannot release unsupported LT write;
- no silent fallback;
- LT C2 tests only after real certification.

## 27. CI và real-runtime policy

Per PR:

- Python unit/integration/security tests;
- Agent UI offscreen tests;
- Host Core tests without Autodesk assemblies;
- JSON schema/golden/canonical digest validation;
- migration checksum/snapshot;
- Phase 0–7 and LT regression;
- formatting/static/package policy checks;
- deterministic fault-injection simulator.

Controlled Windows builder/lab:

- R25 compile against approved Autodesk references;
- deterministic package/version/hash;
- signing where available;
- trusted install;
- Mechanical 2025 full drop matrix;
- AutoCAD crash/close/reopen evidence;
- Agent + Portal approval demo;
- rollback demo;
- publish evidence only after operator approval.

GitHub hosted CI không chứa Autodesk/signing secret và không tự phát hành production DLL/installer.

## 28. Observability và privacy

Metrics bounded, không chứa drawing content:

- consent requested/approved/denied/expired/invalidated;
- approval channel/assurance;
- intent-to-release latency;
- job state/drop point/reconcile outcome;
- receipt reconstruction success;
- unknown/needs-attention rate;
- recovery case age;
- rollback prepared/succeeded/conflicted;
- runtime/release/package cohort;
- kill-switch activation.

Audit event phải có actor, owner, device, document short ID, intent/consent/job/command, runtime/package, correlation ID và reason code.

Không log:

- token/device key/pipe secret;
- full DWG path/content;
- raw program ngoài bounded/redacted summary;
- approval screenshots;
- arbitrary Host payload;
- memory dump mặc định.

## 29. Definition of Done

Phase 8 đạt khi tất cả mục sau đúng:

1. Execution intent immutable và owner-scoped.
2. Approval không thể thực hiện qua public MCP/model field.
3. Agent và Portal dùng cùng một consent truth.
4. Medium local confirmation bind đúng active paired device/session.
5. High assurance Portal flow có CSRF + recent-auth.
6. Consent bind exact user/device/document/program/preview/runtime/package/registry/policy/TTL.
7. Binding change invalid consent trước execution.
8. Consent approve/deny/expire/invalidate race dùng CAS an toàn.
9. Một consent release tối đa một job.
10. Consume + job creation atomic.
11. No Agent command trước required approval.
12. Gateway/Agent/Host execution evidence hashes khớp.
13. Agent ledger sống qua restart và ghi terminal trước send.
14. Host receipt sống qua Host/AutoCAD restart + DWG reopen.
15. Every named drop point không tạo duplicate effect.
16. Only exact `not_started` evidence permits redispatch.
17. Started/unknown write không auto retry.
18. Committed receipt reconstructs terminal success sau lost result.
19. Contradictory evidence chuyển needs-attention.
20. Needs-attention không tự biến success/failed do timer.
21. Recovery case có ordered timeline và safe actions.
22. Operator không có force-success/force-retry control.
23. Checkpoint materialize atomic với commit result hoặc fail toàn bộ.
24. `cad_prepare_rollback` không mutate drawing.
25. Rollback bind exact checkpoint/preview/runtime/package/policy.
26. Rollback conflict fail closed, không generic Undo.
27. Exact rollback duplicate không apply lần hai.
28. Rollback receipt sống qua restart/reopen.
29. Hard pause/write lock/revoke hoạt động đúng ở approval, execution và recovery.
30. Runtime fallback không xảy ra cho commit/rollback.
31. LT không công bố C2 write nếu chưa real-certified.
32. Owner A không đọc/approve/recover/rollback record của owner B.
33. Public errors/resources/logs/diagnostics không rò secret hoặc drawing content.
34. Global/runtime/cohort/high-risk/rollback kill switches fail closed.
35. Phase 0–7 read/write contracts và LT read path không regression.
36. Mechanical 2025 real demo approval → commit → lost-result recovery → rollback thành công.
37. Evidence report ghi commit SHA, package hash, test counts, DWG fixture, drop matrix và operator result.
38. Phase 6 isolation và Phase 7 write gates đạt trước khi mô tả C2 là customer-ready.

## 30. NO-GO criteria

NO-GO nếu có một trong các điều sau:

- model/public MCP có thể approve hoặc hạ assurance;
- approval không bind exact preview/runtime/package/document;
- stale/expired consent vẫn release job;
- một consent có thể tạo hai jobs;
- approval action tạo partial consumed record nhưng không có traceable job;
- Agent command được gửi trước required approval;
- started write bị tự retry;
- any named drop point có thể duplicate effect;
- success được suy từ revision/process presence mà không có receipt;
- Host recovery query có side effect;
- contradictory evidence bị che thành failed/succeeded;
- operator có force-success hoặc generic retry write;
- checkpoint/result materialization không atomic;
- rollback dùng generic Undo/ESC/command string;
- rollback tiếp tục sau revision/entity/layer conflict;
- runtime/package/registry change không invalid rollback preview;
- exact rollback duplicate apply lần hai;
- owner isolation fail ở intent/consent/checkpoint/recovery;
- revoked/replaced session approve hoặc gửi terminal result hợp lệ;
- Portal/Admin gọi Agent/Host trực tiếp;
- raw command/LISP/C#/DLL/path injection lọt qua;
- LT C2 write bật khi chưa real-certified;
- kill switch fail-open;
- public logs/errors/UI lộ secret/full path/drawing content;
- Phase 0–7 hoặc LT regression hỏng;
- real AutoCAD drop matrix chưa có evidence nhưng release được mô tả customer-ready.

## 31. Rollback Phase 8 rollout

Rollback phải giữ Phase 7 low-risk lab path và observe/query khi an toàn:

1. đặt `AUTOCAD_MCP_C2_ENABLED=0`;
2. đặt approval/recovery/rollback flags về `0`;
3. disable release of pending intents; expire/invalidate safely;
4. disable public rollback tools;
5. giữ immutable intent/consent/checkpoint/recovery audit records;
6. không xóa/destructive rollback migration;
7. Agent trở về Phase 7 conservative write hoặc read-only theo policy;
8. Managed Host giữ read/Phase 7 registry, disable recovery/rollback operation pack;
9. giữ hard pause và global/runtime write kill switches;
10. unknown existing jobs tiếp tục needs-attention, không retry khi feature bị tắt.

## 32. Demo nghiệm thu

Demo chuẩn trên AutoCAD Mechanical 2025:

1. User đã pair đúng Full/.NET device và bật local write.
2. ChatGPT prepare/preview CAD Program create-only.
3. Policy yêu cầu medium confirmation.
4. Gateway trả `approval_required`; chưa có Agent command/effect.
5. Agent hiển thị exact drawing/runtime/preview/object count.
6. User approve một lần; Gateway atomically release đúng một job.
7. Cắt WSS sau AutoCAD transaction commit nhưng trước result.
8. Gateway chuyển recovery state, không retry.
9. Agent/Host reconnect; DWG receipt chứng minh succeeded.
10. Job terminalize success; entity count chỉ tăng một lần.
11. Tạo rollback preview từ checkpoint.
12. User approve rollback.
13. Rollback transaction xóa đúng effect và ghi rollback receipt.
14. Gọi lại exact rollback trả duplicate, không thay đổi lần hai.
15. Lặp lại với drawing revision conflict; rollback bị chặn và trả conflict report.
16. User khác không đọc/approve/recover/rollback bất kỳ record nào.

## 33. Deliverables

- intent/consent/recovery/checkpoint/rollback schemas và golden vectors;
- Gateway domain/repository/services/migrations;
- Agent ledger migration, recovery coordinator và approval UI;
- Portal approval/recovery pages/API;
- Managed Host receipt query và rollback operations;
- two public rollback tools + owner-scoped resources;
- deterministic simulator/fault injection matrix;
- real Mechanical 2025 drop/crash/reopen evidence;
- security/IDOR/CSRF/replay test report;
- operator recovery runbook;
- rollback limitation matrix;
- Phase 8 evidence report và GO/NO-GO checklist.

## 34. Quyết định hoãn sang Phase 9+

- modify/delete/purge/move/copy/rotate/scale/pattern/block operations;
- semantic/non-overlap rollback khi drawing đã thay đổi;
- organization/team approvals;
- skill/workflow orchestration;
- Scene Graph assisted recovery;
- broad LT write parity;
- customer installer/update/pilot UX hoàn chỉnh;
- production SLO, multi-worker scale và ecosystem publishing.

---

Phase 8 hoàn thành khi một write create-only không chỉ “thường chạy đúng”, mà có thể chứng minh chính xác điều gì đã xảy ra sau mất mạng/crash, chỉ chạy sau phê duyệt người thật khi policy yêu cầu, và có thể rollback đúng checkpoint khi không có xung đột. Mọi trường hợp không đủ evidence phải dừng ở `needs_attention`, không được đổi lấy availability bằng retry mù hoặc silent fallback.