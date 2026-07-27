# Phase 6 — Public CAD Program v0 and Managed Write Pilot

> Trạng thái: kế hoạch triển khai chi tiết.
>
> Baseline: `main` sau merge PR #8, commit `a3ddacc5e45fa2a3dbf1966ed2d35f12d04a55a7`.
>
> Tài liệu nguồn:
>
> - [fastmcp-multi-user-autocad-plan.md](./fastmcp-multi-user-autocad-plan.md)
> - [Phase-6-plus.md](./Phase-6-plus.md)
> - [appendix-user-interface.md](./appendix-user-interface.md)
> - [phase5-runtime-foundation-evidence.md](./phase5-runtime-foundation-evidence.md)
> - [phase56-identity-isolation-evidence.md](./phase56-identity-isolation-evidence.md)
>
> Phase 6 chỉ productize write create-only đã có POC trong Phase 5. Không mở broad CAD capability, trusted approval hoàn chỉnh hoặc LT write.

## 1. Executive summary

PR #8 đã đưa vào cùng một baseline:

- RuntimeBroker và Managed .NET Host R25;
- entity observation, document identity và revision evidence;
- CAD Program create-only POC với transaction preview/abort, commit và durable DWG receipt;
- production OAuth pairing, owner isolation và Portal tối thiểu;
- R25 packaging/signing/rollback engineering;
- telemetry fail-open pilot.

Public MCP Gateway vẫn chủ yếu read-only. Phase 6 nối các phần đã có thành public lifecycle:

```text
ChatGPT Web
→ FastMCP public tools
→ owner-scoped CAD Program records
→ durable Gateway job
→ device-authenticated WSS
→ Desktop Agent RuntimeBroker
→ Managed .NET Host R25
→ AutoCAD transaction preview/commit/validate
```

Luồng sản phẩm:

```text
Observe → Prepare → Preview → Commit → Validate
```

Write mặc định vẫn tắt. Chỉ device R25 thuộc explicit lab/pilot allowlist được bật `managed_write` sau khi đạt gate.

## 2. Vì sao đây là Phase 6 mới

Roadmap cũ đặt identity/pairing ở Phase 6. PR #8 đã triển khai phần lớn mục tiêu đó trong Phase 5.6 và có live two-user/two-device evidence. Các gate còn lại như live revoke/re-pair và telemetry soak là gate vận hành trước customer pilot, không còn là một phase kiến trúc độc lập.

Cách đánh số mới:

| Trước PR #8 | Sau PR #8 |
|---|---|
| Phase 5 — Runtime Foundation | Phase 5 — Runtime, Identity and Local Write Foundation |
| Phase 6 — Identity/Pairing | Đã hấp thụ vào Phase 5.6 |
| Phase 7 — CAD Program v0 | **Phase 6 — Public CAD Program v0** |
| Phase 8 — Recovery/Approval/C2 | **Phase 7 — Recovery, Approval and Rollback** |
| Phase 9 — CAD Program v1 | **Phase 8 — CAD Program v1** |

## 3. Mục tiêu

1. Tạo `cad.program/0.2` strict, runtime-neutral và versioned.
2. Expose public flow `prepare → preview → commit → validate`.
3. Lưu program, revision, preview, validation và receipt theo owner.
4. Dispatch typed CAD Program commands qua `cad.agent/2`.
5. Đưa mọi write qua `AgentCore → RuntimeBroker → Managed Host`.
6. Pin exact document, revision, runtime, package, capability, registry và policy.
7. Bảo đảm exact duplicate không tạo duplicate effect.
8. Giữ AutoCAD LT compatibility read-only và không regression.
9. Giữ arbitrary C#/DLL/LISP/command/path/network access bị cấm.
10. Tạo live E2E evidence trên AutoCAD Mechanical 2025.

## 4. Non-goals

Phase 6 không làm:

- modify, delete, erase, purge;
- move, copy, rotate, scale, mirror, offset, trim, fillet hoặc chamfer;
- loop, pattern hoặc expression grammar;
- block editing và broad annotation;
- arbitrary AutoLISP, C#, DLL, shell, Python hoặc AutoCAD command;
- public rollback;
- trusted medium/high-risk approval hoàn chỉnh;
- automatic retry cho write đã bắt đầu;
- AutoCAD LT write;
- AutoCAD 2018–2024 certification;
- skill/workflow engine;
- Scene Graph;
- organization/team sharing, billing hoặc marketplace.

## 5. Dependency gates

### 5.1. Identity và tenant isolation

Trước customer write phải có:

- Auth0 `(issuer, sub)` map đúng internal owner;
- pairing one-time code không replay;
- owner filtering phủ device, session, program, preview, job, resource, artifact và audit;
- revoke đóng active WSS và chặn reconnect;
- replacement session vô hiệu session cũ;
- live revoke/re-pair drill xanh.

Engineering có thể tiếp tục trên lab allowlist khi live revoke/re-pair chưa hoàn tất, nhưng `managed_write` phải giữ false ngoài lab.

### 5.2. Managed Host R25

- package hash/signature hợp lệ trong cohort;
- Host handshake, document identity và revision evidence hợp lệ;
- transaction preview/abort regression xanh;
- durable receipt sống qua Host/Agent restart và DWG reopen;
- operation registry hash được Agent công bố và Gateway pin.

### 5.3. Production release

Customer pilot chưa GO nếu thiếu:

- CA-issued code-signing certificate;
- trusted Authenticode timestamp;
- private-key custody review;
- malware/SBOM/build-provenance approval;
- telemetry soak 3–7 ngày;
- incident và rollback runbook.

## 6. CAD Program contract `cad.program/0.2`

Không sửa ngầm `0.1`. Public Gateway chỉ phát `0.2`; parser `0.1` có thể giữ cho lab regression.

### 6.1. Semantic fields

Program lưu:

- `program_id` do Gateway tạo;
- `program_revision` tăng đơn điệu;
- `device_id`;
- `source_snapshot_id`;
- `document_id`;
- `expected_document_revision`;
- `schema_version`;
- `registry_version`;
- ordered operations;
- preconditions/postconditions;
- budgets;
- `program_digest` do Gateway canonicalize và tính.

Model/client không được tự chọn:

- runtime ID/role;
- Host family/version;
- Agent/Host package hash;
- capability manifest hash;
- operation registry hash;
- policy version;
- execution digest.

### 6.2. Operation set v0

Bắt buộc:

- `ensure_layer`;
- `create_line`;
- `create_circle`;
- `create_polyline`;
- `create_rectangle`;
- `create_text`;
- `create_dimension_linear`.

Quy tắc:

- tuyến tính, không loop/recursion/dynamic code;
- `operation_id` duy nhất;
- operation sau chỉ tham chiếu typed output của operation trước;
- không dùng raw AutoCAD `ObjectId` làm stable public identifier;
- unsupported operation trả `capability_missing`, không approximate;
- Gateway, Agent và Host cùng enforce budgets.

### 6.3. Budgets

Hard ceiling tối thiểu:

- tối đa 256 operations;
- bounded entity/layer count;
- bounded vertices và text bytes;
- coordinate/radius/height limits;
- payload/result/artifact byte limits;
- execution deadline;
- preview TTL;
- một write job đồng thời trên mỗi device/document.

### 6.4. Idempotency

Dùng key riêng cho:

- prepare;
- preview;
- commit;
- validate.

Reuse cùng key với payload khác trả `idempotency_conflict` trước dispatch.

## 7. Public MCP surface

### 7.1. `cad_prepare_program`

Không dispatch Agent.

Input:

- `device_id`;
- `source_snapshot_id`;
- create-only operations;
- optional postconditions/budget overrides;
- optional idempotency key.

Hành vi:

- yêu cầu `autocad.write`;
- owner-check device và snapshot;
- snapshot phải thuộc đúng device/document;
- lấy current runtime/capability evidence;
- canonicalize, validate và tính digest server-side;
- lưu immutable revision 1;
- phân loại risk và missing capability.

Output:

- program ID/revision/digest;
- document/runtime/capability summary;
- risk class;
- missing capabilities;
- resource URI;
- `ready_for_preview`.

### 7.2. `cad_preview`

- revalidate owner, program, document, runtime, package, capability, registry và policy;
- tạo durable job `kind=program_preview`;
- không silent fallback runtime;
- Host transaction phải abort;
- preview record lưu atomically với terminal job result.

Output gồm preview ID, exact binding, planned entity counts, validation summary, expiry, job/resource URI.

Preview không phải human approval.

### 7.3. `cad_commit`

Chỉ low-risk create-only trong explicit pilot cohort.

Điều kiện:

- exact preview còn hiệu lực;
- program/document/runtime/package/capability/registry/policy không đổi;
- Agent write lock bật và hard pause tắt;
- `managed_write` bật cho device/cohort;
- không có write khác trên cùng document.

Không retry mù sau evidence `started`. Exact duplicate chỉ được trả prior receipt khi digest/binding khớp.

### 7.4. `cad_validate`

Read-only validation cho:

- entity count/type/layer;
- bounded geometry/bounds;
- document revision;
- receipt/program/execution binding;
- postconditions.

### 7.5. Resources

```text
cad://programs/{program_id}/revisions/{revision}
cad://previews/{preview_id}
cad://jobs/{job_id}
cad://validations/{validation_id}
cad://receipts/{receipt_id}
```

Mọi resource owner-scoped và bounded.

## 8. Gateway domain và storage

Migration additive dự kiến: `0005_phase6_programs.sql`.

Records:

| Record | Vai trò |
|---|---|
| `cad_programs` | owner/device/document root |
| `cad_program_revisions` | immutable semantic revision |
| `cad_previews` | preview + exact execution binding |
| `cad_validations` | bounded validation report |
| `cad_execution_receipts` | Gateway copy của Agent/Host evidence |
| `program_idempotency` | key theo action |

Principal luôn lấy từ authenticated context. Browser/model không gửi `owner_id` đáng tin cậy.

Cross-owner ID trả `not_found` trước dispatch và không tiết lộ record có tồn tại.

## 9. Gateway–Agent protocol

Bỏ product use của `write_fixture`. Bổ sung typed kinds:

```text
program_preview
program_commit
program_validate
```

Command pin:

- program/execution digest;
- document identity/revision;
- runtime/package/capability/registry/policy;
- effect class;
- deadline;
- idempotency key;
- payload hash.

Agent từ chối trước Host khi write lock tắt, hard pause bật, runtime/document/revision/binding sai, capability thiếu, deadline hết hạn hoặc conflicting duplicate.

## 10. Desktop Agent

Tách boundary:

```text
ReadCommandExecutor
ProgramCommandExecutor
```

`ProgramCommandExecutor`:

- chỉ nhận typed commands;
- gọi `RuntimeBroker.select_write_runtime`;
- giữ per-document write mutex;
- ghi accepted/started/terminal vào local ledger;
- persist terminal evidence trước gửi Gateway;
- không retry write đã started;
- không gọi AutoCAD API trực tiếp.

Agent UI tối thiểu hiển thị runtime, document, write lock, hard pause, active job, mismatch, `outcome_unknown` và support ID.

## 11. Managed .NET Host R25

- parse/validate `cad.program/0.2`;
- validate Host session, sequence, deadline, payload hash;
- validate document/revision/runtime/registry/budget;
- preview bằng transaction abort;
- commit trong một transaction;
- ghi durable receipt cùng effect khi khả thi;
- exact duplicate trả receipt cũ;
- conflicting duplicate fail closed;
- validate bằng read-only query;
- không network/OAuth/tenant logic;
- không reflection dispatch hoặc arbitrary path/code/assembly.

Phase 6 chỉ mở write trên R25/Mechanical 2025 đã có evidence. Family cũ và LT vẫn unsupported/uncertified cho write.

## 12. UI và Portal

Portal chỉ cần:

- program/preview/result summary;
- runtime/package/capability binding;
- activity/job links;
- capability missing và invalidation reason;
- operator kill-switch state.

Không có approval button trong Phase 6. Trusted approval là security boundary của Phase 7; không tạo MCP tool `cad_approve` hoặc `confirm=true`.

## 13. Feature flags

```text
AUTOCAD_MCP_PROGRAM_V0_ENABLED=0
AUTOCAD_MCP_MANAGED_WRITE_ENABLED=0
AUTOCAD_MCP_LT_WRITE_ENABLED=0
AUTOCAD_MCP_HIGH_RISK_ENABLED=0
AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS=
```

- mặc định write off;
- `managed_write` chỉ bật explicit R25 cohort;
- `lt_write` luôn false trong Phase 6;
- read path không phụ thuộc write flags;
- policy/runtime/package change invalid preview.

## 14. Phân chia PR

1. **Contracts và docs** — roadmap normalization, `cad.program/0.2`, golden/digest tests.
2. **Gateway lifecycle/storage** — migration, owner-scoped services, idempotency và policy.
3. **Public FastMCP surface** — four tools/resources, scopes, schema snapshots.
4. **Agent protocol/executor** — typed commands, write mutex, ledger, broker admission.
5. **Managed Host integration** — registry, rectangle/text/dimension, receipt và failure tests.
6. **UI/E2E/evidence** — Agent/Portal states, Mechanical 2025 live run, security review, GO/NO-GO.

Mỗi PR độc lập, giữ tests xanh và rollback được. Không gom toàn Phase 6 thành một PR lớn.

## 15. Test matrix

### Contract

- strict schema và unknown fields;
- cross-language canonical digest;
- output/forward references;
- budget/property fuzz;
- NaN/infinite/oversized values;
- unsupported operation.

### Gateway

- prepare không dispatch;
- preview atomically materialized;
- stale snapshot/document;
- runtime/package/registry/policy invalidation;
- owner isolation và resource IDOR;
- idempotency conflict;
- one write per document;
- scope/feature-flag denial.

### Agent

- write lock/hard pause;
- wrong runtime/document/revision;
- expired deadline/payload mismatch;
- exact/conflicting duplicate;
- Host unavailable;
- restart và terminal persist-before-send.

### Managed Host

- preview abort no-effect;
- commit expected geometry;
- duplicate no second effect;
- receipt survives restart/reopen;
- exception rolls transaction back;
- document switch/busy/modal;
- invalid package/registry/budget;
- no arbitrary code/path.

### Regression

- all public read tools;
- Phase 3 durable state machine;
- Phase 4 AutoLISP/File IPC;
- Phase 5 identity/pairing/Portal;
- telemetry fail-open;
- packaging/signing validators.

## 16. Live acceptance scenario

Dùng `drawing33.dwg` trên Mechanical 2025:

1. Auth0 user chỉ list đúng paired device.
2. `cad_observe` tạo snapshot/revision.
3. Prepare rectangle + four circles + layer + text/dimension.
4. Prepare không đổi DWG.
5. Preview transaction abort, drawing unchanged.
6. Commit tạo đúng geometry một lần.
7. Exact retry không tạo entity lần hai.
8. Validate postconditions.
9. Manual drawing change invalidates old preview.
10. Runtime/package/policy change invalidates old preview.
11. Hard pause hoặc kill switch chặn trước Host.
12. Other owner guesses IDs and receives `not_found`.
13. LT/compatibility device receives `capability_missing` for write.

## 17. GO/NO-GO

### Engineering GO

- four-tool lifecycle owner-scoped;
- preview no-effect;
- commit exact-once by receipt evidence;
- stale/binding changes fail closed;
- Agent lock/pause enforced;
- R25 live E2E green;
- LT/read regressions green;
- arbitrary code boundary intact;
- evidence and rollback runbook complete.

### Customer pilot GO

Ngoài Engineering GO còn cần production signing/timestamp/provenance, live revoke/re-pair, telemetry soak, production config review, support ownership và explicit pilot cohort.

## 18. Rollback

- disable `managed_write`;
- hide/disable four public write tools in production profile;
- retain records for audit;
- never retry started/unknown write blindly;
- Agent returns read-only;
- Managed Host continues read-only observe;
- LT compatibility unchanged;
- migrations additive, không xóa production records;
- rollback package to previous-known-good when package-caused.

## 19. Definition of Done

Phase 6 hoàn tất khi ChatGPT có thể prepare một CAD Program create-only, preview không đổi bản vẽ, commit đúng một lần và validate kết quả trên AutoCAD Mechanical 2025 qua Managed .NET; toàn bộ lifecycle được owner-scope, runtime-pin, audit và fail closed, trong khi AutoCAD LT và public read path không regression.
