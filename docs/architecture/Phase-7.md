# Phase 7 — CAD Program v0 and Trusted Write Path

> Trạng thái: kế hoạch triển khai chi tiết.
>
> Baseline: nhánh `phase-5`, sau commit Phase 5 Managed Runtime Foundation.
>
> Tài liệu nguồn:
>
> - [fastmcp-multi-user-autocad-plan.md](./fastmcp-multi-user-autocad-plan.md)
> - [Phase-6-plus.md](./Phase-6-plus.md)
> - [appendix-user-interface.md](./appendix-user-interface.md)
> - [phase5-runtime-foundation-evidence.md](./phase5-runtime-foundation-evidence.md)
>
> Phase 7 chỉ được mở cho người dùng ngoài lab sau khi Phase 6 identity, pairing, revoke và tenant isolation đạt exit gate.

## 1. Executive summary

Phase 5 đã chứng minh trên AutoCAD Mechanical 2025 thật rằng Managed .NET Host có thể:

- validate một CAD Program create-only;
- preview bằng database transaction rồi abort;
- commit trực tiếp bằng AutoCAD Managed .NET API;
- tạo durable DWG receipt;
- nhận diện duplicate sau Agent/Host/AutoCAD restart mà không áp effect lần hai.

Tuy nhiên đây mới là POC cục bộ giữa Desktop Agent và Managed Host. Public Gateway hiện vẫn là read-only; CAD Program chưa có owner-scoped lifecycle, public resources, policy gate, write OAuth scope, durable preview record hoặc runtime-neutral execution path hoàn chỉnh.

Phase 7 phải **productize** POC đó thành tuyến sau:

```text
ChatGPT Web
→ FastMCP public write tools
→ owner-scoped CAD Program + preview records
→ durable Gateway job
→ device-authenticated WSS
→ Desktop Agent RuntimeBroker
→ Managed .NET Host primary
→ AutoCAD transaction preview/commit/validate
```

AutoCAD LT chỉ được mở write khi một portable subset đã qua real smoke và capability manifest công bố chính xác. Nếu chưa có evidence, LT tiếp tục read-only và trả `capability_missing`; không được giả lập parity hoặc silent fallback.

## 2. Baseline assessment trên nhánh `phase-5`

### 2.1. Phần đã có và phải tái sử dụng

- `packages/host_contracts/schemas/cad-program-0.1.schema.json` có create-only schema.
- `AutocadMcp.Host.Core/CadProgram.cs` có parser, canonical digest, runtime binding và allowlisted operation registry.
- `AutocadMcp.Host.R25/AutoCadProgramOperations.cs` có validate, transaction preview/abort, commit, checkpoint và durable receipt.
- `DrawingProgramLedger.cs` giữ idempotency receipt trong DWG.
- `cad.host/1` đã có authenticated Named Pipe, bounded frame, sequence, deadline và payload hash.
- Gateway đã có durable job state machine, reconnect semantics, owner-filtered repository và runtime evidence materialization.
- Agent đã có `RuntimeBroker`, local ledger, hard pause, diagnostics và runtime/package evidence.
- Managed write, LT write, high risk và arbitrary code hiện fail closed bằng policy.

### 2.2. Khoảng trống phải đóng trong Phase 7

1. Public MCP surface chưa có `cad_prepare_program`, `cad_preview`, `cad_commit`, `cad_validate`.
2. `GatewayConfig` và tool annotations vẫn mô tả public surface read-only.
3. `CommandMessage.kind` chưa có typed CAD Program command; hiện chỉ có `observe` và test fixture.
4. Agent Managed .NET adapter vẫn là read-oriented facade và chưa đi qua durable public write executor.
5. Capability manifest của Agent chưa công bố đầy đủ primitive, preview, commit, validate và registry hash.
6. CAD Program 0.1 khóa `runtime_id=managed_dotnet`, gắn idempotency vào program và chưa có program revision, output reference, rectangle hoặc linear dimension.
7. Gateway chưa lưu CAD Program, program revision, preview binding, validation result hoặc commit receipt ở cấp owner.
8. Preview/commit chưa bind đầy đủ policy version, capability manifest hash và operation registry hash.
9. LT write chưa có real certification; không được mở chỉ dựa trên automated regression.
10. Phase 6 live two-user/two-device/revoke gate chưa có external evidence.

## 3. Mục tiêu Phase 7

1. Cung cấp CAD Program v0 runtime-neutral, bounded và versioned cho create-only low-risk operations.
2. Tạo owner-scoped lifecycle: prepare → preview → commit → validate.
3. Pin exact document, program revision, runtime, package, capability, registry và policy vào preview/commit.
4. Dùng durable Gateway job cho mọi tác vụ chạm CAD runtime.
5. Không retry write mù; duplicate exact payload không tạo effect lần hai.
6. Managed .NET là primary write path cho AutoCAD Full.
7. LT chỉ chạy portable subset khi được manifest và policy cho phép.
8. Giữ toàn bộ Phase 0–6, read tools, LT compatibility và local profile không regression.
9. Giữ arbitrary C#/DLL/LISP/command/path/network access bị cấm.
10. Tạo evidence đủ rõ để Phase 8 tiếp tục recovery, trusted approval và rollback.

## 4. Dependency gates

### 4.1. Gate bắt buộc trước customer write

Phase 6 phải chứng minh:

- Auth0 `(issuer, sub)` map đúng internal user;
- production pairing one-time code không replay;
- owner filtering phủ device, session, job, snapshot, artifact, program, preview và audit;
- revoke đóng active WSS và chặn reconnect;
- session cũ không gửi result sau replacement;
- two-user/two-device isolation chạy thật.

Có thể phát triển và test Phase 7 trên lab owner trước khi Phase 6 hoàn tất, nhưng `managed_write` phải giữ false ngoài explicit lab allowlist.

### 4.2. Gate Managed .NET

- R25 Host/Agent package hash hợp lệ.
- Host handshake và capability manifest hợp lệ.
- Active document có stable document identity và strong-enough revision evidence.
- Transaction preview/abort và durable receipt regression xanh.
- `managed_write` kill switch được bật riêng cho cohort được phép.

### 4.3. Gate AutoCAD LT

`lt_write` chỉ được bật khi:

- AutoCAD LT 2024+ thật đã chạy smoke;
- packaged dispatcher/compiler target được allowlist;
- portable primitive conformance suite xanh;
- preview strategy, revision strength và duplicate semantics được ghi rõ;
- không có Managed .NET component load vào LT.

Không đạt gate này không chặn Managed .NET Phase 7 exit; LT tiếp tục read-only với limitation được công bố rõ.

## 5. Non-goals

Phase 7 không làm:

- trusted medium/high-risk approval hoàn chỉnh;
- recovery đầy đủ cho mọi network/crash drop point;
- rollback/checkpoint restore public;
- modify, delete, erase, purge, move, copy, pattern hoặc block editing;
- arbitrary AutoLISP, C#, DLL, shell, Python hoặc AutoCAD command;
- skill marketplace, workflow engine hoặc Scene Graph;
- broad organization/team sharing;
- billing/subscription;
- production auto-update ecosystem;
- một CAD editor hoặc DWG viewer riêng trong Portal.

Các mục recovery/approval/rollback thuộc Phase 8; broad CAD capability thuộc Phase 9.

## 6. Quyết định contract

### 6.1. Tạo `cad.program/0.2`

Không sửa ngầm semantics của `cad.program/0.1`. Schema 0.1 có `additionalProperties=false`, runtime binding Managed .NET cố định và thiếu metadata product lifecycle. Phase 7 tạo contract `cad.program/0.2`; Host có thể giữ parser 0.1 cho lab regression nhưng public Gateway chỉ phát 0.2.

### 6.2. Tách program semantic khỏi execution binding

CAD Program semantic lưu:

- `program_id` do Gateway tạo;
- `program_revision` tăng đơn điệu;
- `device_id`;
- `source_snapshot_id`;
- `document_id`;
- `expected_document_revision`;
- `registry_version`;
- `operations`;
- `preconditions`;
- `postconditions`;
- `budgets`;
- `program_digest` do Gateway canonicalize và tính.

Không cho client/model tự chọn:

- runtime ID/role;
- Host family/version;
- package hash;
- capability manifest hash;
- operation registry hash;
- policy version;
- execution digest.

Các giá trị này do Gateway pin từ owner-scoped device/snapshot evidence khi prepare hoặc preview.

### 6.3. Idempotency thuộc từng execution

Không dùng một `idempotency_key` duy nhất như thuộc tính semantic của program. Dùng key riêng cho:

- prepare request;
- preview execution;
- commit execution;
- validate execution.

Reuse cùng key với payload khác phải trả `idempotency_conflict` trước dispatch.

### 6.4. Operation set v0

Bắt buộc:

- `ensure_layer`;
- `create_line`;
- `create_circle`;
- `create_polyline`;
- `create_rectangle`;
- `create_text`;
- `create_dimension_linear`.

Quy tắc:

- step tuyến tính;
- không loop, recursion, dynamic code hoặc unbounded collection;
- `operation_id` duy nhất;
- operation sau chỉ được tham chiếu output của operation trước;
- reference phải typed và không truy cập entity ngoài program nếu không có immutable snapshot ref;
- unsupported operation trả `capability_missing`, không tự đổi sang primitive gần giống.

### 6.5. Output references

Minimum output registry:

- `ensure_layer` → logical layer ref;
- create entity → logical entity ref và bounded geometry summary;
- rectangle → four corners/polyline ref;
- dimension → dimension entity ref.

Host chỉ trả handles/object IDs như evidence; public contract không coi raw AutoCAD `ObjectId` là stable cross-session identifier.

### 6.6. Budgets

Gateway, Agent và Host cùng enforce:

- tối đa 256 operations;
- tối đa entity/layer tạo mới;
- tối đa vertex/text bytes;
- coordinate/radius/height bounds;
- payload bytes;
- execution deadline;
- result/artifact bytes;
- preview TTL;
- maximum concurrent write job trên một device/document là 1.

Mức thực tế phải cấu hình được nhưng không vượt hard ceiling trong shared contract.

## 7. Public MCP surface

### 7.1. `cad_prepare_program`

Input tối thiểu:

- `device_id`;
- `source_snapshot_id`;
- create-only operations;
- optional postconditions/budget overrides trong giới hạn;
- optional request idempotency key.

Hành vi:

- yêu cầu scope `autocad.write`;
- owner-check device và snapshot;
- snapshot phải thuộc đúng device/document;
- lấy current capability/runtime evidence;
- canonicalize và validate program;
- tính digest server-side;
- lưu immutable revision 1;
- không dispatch tới Agent.

Output:

- `program_id`;
- `program_revision`;
- `program_digest`;
- document/runtime/capability summary;
- risk class;
- missing capabilities;
- program resource URI;
- `ready_for_preview`.

### 7.2. `cad_preview`

Input:

- `program_id`;
- `program_revision`;
- idempotency key.

Hành vi:

- scope `autocad.write`;
- exact owner/program/device check;
- revalidate current document/runtime/capability/package/policy;
- tạo durable job `kind=program_preview`, `effect_class=write` theo hướng conservative;
- không silent fallback runtime;
- Host transaction phải abort;
- lưu preview record atomically với terminal job result.

Output:

- `preview_id`;
- program/execution digest;
- document before/after revision;
- exact runtime/package/registry/policy binding;
- planned entity/layer counts;
- validation summary;
- preview expiry;
- preview resource URI và job URI.

Preview không được mô tả là human approval.

### 7.3. `cad_commit`

Input:

- `preview_id`;
- expected `program_id` và `program_revision`;
- idempotency key.

Hành vi:

- scope `autocad.write`;
- low-risk create-only policy;
- local Agent write lock phải enabled;
- exact preview còn hiệu lực;
- bind owner, device, document, program digest, execution digest, runtime, package, capability manifest, registry, policy và TTL;
- tạo durable job `kind=program_commit`, `effect_class=write`;
- không retry sau ACK/started khi outcome chưa chứng minh;
- exact duplicate trả durable prior result;
- payload mismatch fail closed.

Output:

- commit job/result;
- `checkpoint_id` chỉ là evidence cho Phase 8, chưa phải public rollback guarantee;
- before/after revision;
- created entity/layer counts;
- durable receipt evidence;
- validation/resource links.

### 7.4. `cad_validate`

Cho phép hai mode bounded:

- validate program trước preview;
- validate committed result theo receipt/postconditions.

Validation chạm live document phải đi durable job và pin exact evidence. Không dùng ezdxf/headless validation để authorize live commit.

### 7.5. Tool annotations

Thay helper read-only hiện tại bằng annotation theo từng tool:

| Tool | readOnlyHint | idempotentHint | destructiveHint |
|---|---:|---:|---:|
| `cad_prepare_program` | false | theo idempotency key | false |
| `cad_preview` | false | true với exact key | false |
| `cad_commit` | false | true với exact key | false cho create-only |
| `cad_validate` | phụ thuộc mode | true | false |

Không đánh dấu write tool là read-only chỉ vì create-only không xóa entity.

## 8. Gateway design

### 8.1. Domain models

Thêm domain records:

- `CadProgramRecord`;
- `CadProgramRevision`;
- `CadPreviewRecord`;
- `CadExecutionBinding`;
- `CadValidationRecord`;
- `CadProgramEvent` nếu cần ordered audit ngoài job event.

Invariant:

- owner/device/document không đổi giữa revisions;
- program revision immutable sau khi tạo;
- patch tạo revision mới và digest mới;
- preview chỉ trỏ một exact program revision;
- commit chỉ trỏ một exact preview;
- preview/commit result không thể đổi owner hoặc device;
- terminal record/result ghi atomic với job transition.

### 8.2. SQLite migrations

Dùng migration runner/checksum hiện có; thêm additive migration cho:

- `cad_programs`;
- `cad_program_revisions`;
- `cad_previews`;
- `cad_validations` hoặc normalized execution result table;
- indexes owner/program/device/document/status/expiry;
- unique idempotency identity theo owner + action + key;
- foreign keys tới owner-scoped device/job/snapshot khi repository model cho phép.

Không sửa migration cũ đã áp dụng.

### 8.3. Application services

Tách khỏi `app.py`:

- `CadProgramService.prepare()`;
- `CadProgramService.preview()`;
- `CadProgramService.commit()`;
- `CadProgramService.validate()`;
- `CadProgramPolicyService`;
- `CadProgramMaterializer` cho atomic result/resource creation.

FastMCP facade chỉ parse typed input, resolve principal/correlation ID và map safe errors.

### 8.4. Durable job extension

Mở typed kinds:

- `observe`;
- `program_preview`;
- `program_commit`;
- `program_validate`.

Không giữ `write_fixture` trong production public path.

Capability check trước claim và ngay trước send:

- operation capability;
- runtime role;
- package/hash;
- capability manifest hash;
- registry hash;
- local write enabled;
- global/runtime/cohort kill switches.

Preview và commit không share cùng command ID hoặc idempotency identity.

### 8.5. Public resources

Owner-scoped resources tối thiểu:

- `cad://program/{program_id}`;
- `cad://program/{program_id}/revision/{revision}`;
- `cad://preview/{preview_id}`;
- existing `cad://job/{job_id}` mở rộng cho write evidence;
- validation result URI khi payload lớn.

Resource output phải bounded, redact internal pipe/session/secret/path và không trả raw DWG content.

## 9. Shared contracts và protocol

### 9.1. `cad.agent/1`

Giữ protocol version nếu thay đổi additive và older Agent reject capability đúng. Mở `CommandMessage.kind` cho typed CAD Program kinds; không dùng generic `command` string do model cung cấp.

Command payload phải chứa:

- canonical program revision;
- program digest;
- action-specific idempotency key;
- exact execution binding;
- expected document revision;
- budgets/deadline;
- preview binding cho commit.

### 9.2. Capability manifest

Công bố granular capabilities, ví dụ:

```text
program.v0.validate
program.v0.preview
program.v0.commit
program.v0.ensure_layer
program.v0.create_line
program.v0.create_circle
program.v0.create_polyline
program.v0.create_rectangle
program.v0.create_text
program.v0.create_dimension_linear
```

Manifest phải có:

- registry version;
- registry hash;
- runtime ID/role;
- package ID/version/hash;
- preview strategy;
- revision strength;
- capability tier (`portable_core` hoặc `managed_standard`).

### 9.3. Execution digest

Execution digest phải bao gồm canonical:

- program digest;
- program revision;
- document identity/revision;
- runtime ID/role;
- Host/compiler family/version;
- package hash;
- capability manifest hash;
- operation registry version/hash;
- risk policy version;
- preview strategy.

Bất kỳ thay đổi nào trong các giá trị này làm preview cũ invalid.

## 10. Desktop Agent design

### 10.1. Runtime-neutral adapter

Tạo hoặc refactor thành `CadRuntimeAdapter` với các method bounded:

- `probe()`;
- `observe()`;
- `validate_program()`;
- `preview_program()`;
- `commit_program()`.

Giữ compatibility alias cho read port để Phase 4/5 tests không bị rewrite rộng.

### 10.2. Managed .NET adapter

- gọi exact `cad.host/1` operation IDs;
- verify handshake, capability, package, registry và result hash;
- không expose generic `_command` cho payload tùy ý ở public layer;
- timeout/deadline riêng cho preview/commit;
- map safe Host errors sang shared error codes;
- invalidate cached handshake khi Host reload/package mismatch.

### 10.3. RuntimeBroker

- select runtime theo capability + policy, không chỉ availability;
- pin selection trước dispatch;
- write không fallback;
- read fallback chỉ khi policy cho phép và phải hiện degraded;
- LT write only khi exact portable capabilities có trong manifest.

### 10.4. Agent executor và ledger

- validate command kind/effect/payload trước ACK;
- persist `accepted` trước Host execution;
- persist `started` trước transaction call;
- persist terminal result/receipt hash;
- exact duplicate trả prior terminal result;
- conflicting duplicate trả reject;
- unknown started write không tự retry;
- hard pause/local write lock chặn trước Host;
- revoke/session replacement invalidates admission.

Phase 7 giữ conservative `outcome_unknown/needs_attention`; operator recovery đầy đủ thuộc Phase 8.

## 11. Managed Host design

### 11.1. Registry và parser

- thêm `cad.program/0.2` parser trong Host Core;
- giữ strict exact-object validation;
- registry explicit, không reflection dispatch;
- tính registry hash deterministic cross-language;
- golden vectors cho Unicode, timestamp, floating point và output refs.

### 11.2. Primitive implementation

- giữ operation hiện có;
- thêm rectangle và linear dimension;
- implement output ref resolution chỉ từ step trước;
- create entity trong model space theo v0 scope;
- validate layer, finite geometry, zero-length, degeneracy và dimension points;
- không mở arbitrary block, style, file path hoặc command invocation.

### 11.3. Preview

- document lock;
- exact revision check;
- one database transaction;
- apply program;
- collect bounded planned outputs/postcondition evidence;
- abort;
- prove revision unchanged;
- trả exact execution binding.

Nếu abort/revision proof không chắc chắn, trả `preview_abort_failed`; không tạo preview record thành công.

### 11.4. Commit

- exact preview binding;
- check durable receipt trước document mismatch để duplicate sau reopen có thể reconcile;
- exact revision check trước first effect;
- apply all operations và receipt trong cùng transaction;
- commit một lần;
- collect created entity evidence;
- verify revision advanced khi có effect;
- return bounded receipt/checkpoint/postcondition result.

### 11.5. Validate

- preflight schema/capability/revision/runtime;
- post-commit validation dùng created handles/receipt và bounded geometry summary;
- không quét toàn bộ DWG không giới hạn;
- validation failure không được rewrite commit result thành success.

## 12. AutoCAD LT compatibility

Phase 7 không được compile mọi program sang raw LISP mặc định.

Nếu LT portable subset được triển khai:

- compiler/packaged operation registry nằm trong repo và versioned;
- only exact portable primitive IDs;
- generated text không nhận arbitrary expression/command/path;
- preview strategy và limitation được ghi trong evidence;
- geometry result so sánh với .NET trong tolerance;
- same public tool shape, runtime-specific evidence khác được phép;
- write flag độc lập `lt_write`.

Nếu chưa có real LT evidence:

- manifest không công bố write capabilities;
- public prepare có thể báo missing capability;
- preview/commit trả `capability_missing` trước dispatch;
- observe/query hiện tại vẫn chạy.

## 13. UI/UX tối thiểu

### 13.1. ChatGPT Web

Trả summary dễ hiểu:

- chương trình sẽ tạo gì;
- số entity/layer dự kiến;
- runtime đang dùng;
- preview có còn hiệu lực không;
- commit thành công/duplicate/unknown;
- resource links để xem chi tiết bounded.

Không gọi preview là approval và không giấu runtime degradation.

### 13.2. Desktop Agent

Bổ sung:

- local write enabled/disabled;
- current write job;
- program/preview short ID;
- runtime/package/registry summary;
- hard pause;
- cảnh báo `outcome_unknown`;
- support code an toàn.

Không hiển thị raw payload, pipe secret, full path hoặc drawing content trong diagnostics mặc định.

### 13.3. Portal

Phase 7 chỉ cần read-only program/job history và device write capability status nếu Phase 6 Portal đã có. Trusted confirmation/approval UI thuộc Phase 8.

## 14. Security controls

1. `autocad.write` bắt buộc cho four tools.
2. Owner filter ở mọi repository lookup và resource read.
3. Device/session/runtime/package/capability binding kiểm tại Gateway, Agent và Host.
4. Model không được cung cấp runtime/package/policy digest.
5. Không arbitrary code, command, assembly, path hoặc network.
6. Payload/result/frame/deadline budgets ở cả ba boundary.
7. One active write per device/document.
8. No silent runtime fallback.
9. No write retry after `started` nếu chưa có terminal receipt.
10. Duplicate exact payload idempotent; conflicting payload rejected.
11. Global, runtime và cohort kill switches fail closed.
12. Audit ghi actor, owner, device, document, program, preview, job, runtime và correlation ID.
13. Logs không chứa DWG content, user text vượt bounds, secret, token hoặc pipe proof.
14. Public errors dùng allowlist và correlation ID; stack trace chỉ trong local support bundle đã redaction.

## 15. Feature flags và rollout

Tối thiểu:

```text
AUTOCAD_MCP_PROGRAM_V0_ENABLED=0|1
AUTOCAD_MCP_MANAGED_WRITE_ENABLED=0|1
AUTOCAD_MCP_LT_WRITE_ENABLED=0|1
AUTOCAD_MCP_LOW_RISK_COMMIT_ENABLED=0|1
AUTOCAD_MCP_PHASE7_LAB_OWNER_ALLOWLIST=...
```

Giữ:

```text
AUTOCAD_MCP_PHASE4_WRITE_DISABLED=1
```

hoặc thay bằng một global write kill switch có migration/backward compatibility rõ. Không để hai flag mâu thuẫn tạo fail-open; effective policy luôn lấy giá trị hạn chế nhất.

Rollout order:

1. contract/core tests only;
2. local Host lab;
3. one explicit lab owner/device;
4. Phase 6 two-user isolation lab;
5. internal canary low-risk create-only;
6. customer pilot chỉ sau Phase 8 approval/recovery gate nếu policy yêu cầu.

## 16. Implementation work packages

### 7.0 — Contract freeze và threat review

- chốt `cad.program/0.2`;
- chốt operation/output refs/budgets;
- chốt execution digest;
- threat model public write path;
- golden cross-language vectors.

**Exit:** Python/C# parse và hash giống nhau; invalid corpus fail giống nhau.

### 7.1 — Gateway program persistence

- migrations;
- owner-scoped repositories;
- domain invariants;
- `CadProgramService.prepare`;
- program resources;
- idempotency tests.

**Exit:** prepare không chạm Agent nhưng lưu exact immutable revision/digest an toàn.

### 7.2 — Protocol, capability và Agent write seam

- extend shared protocol kinds;
- publish granular capability/registry evidence;
- runtime-neutral adapter;
- broker write selection;
- local write lock;
- ledger states.

**Exit:** Agent nhận/reject typed preview command đúng policy mà chưa mở public commit.

### 7.3 — Public preview

- `cad_preview` tool;
- durable preview job;
- Host 0.2 preview;
- atomic preview materialization;
- preview resource/UI summary;
- stale/runtime/package/policy invalidation.

**Exit:** public preview trên Mechanical 2025 abort sạch và lưu exact binding.

### 7.4 — Public commit

- `cad_commit` tool;
- exact preview enforcement;
- Host commit/receipt;
- duplicate/reconnect mapping;
- postcondition evidence;
- write job/resource output.

**Exit:** exact commit tạo effect một lần; immediate/restart duplicate không tạo effect lần hai.

### 7.5 — Validate và primitive parity

- rectangle;
- linear dimension;
- output refs;
- pre/post validation;
- cross-language schema/property tests;
- managed real DWG fixtures.

**Exit:** demo tấm chữ nhật bốn lỗ và annotation/dimension tối thiểu chạy preview/commit/validate.

### 7.6 — LT gate

- implement portable compiler/packaged ops nếu được ưu tiên;
- real LT 2024+ smoke;
- conformance evidence;
- hoặc giữ explicit read-only limitation.

**Exit:** LT write chỉ bật nếu evidence thật; nếu không, limitation matrix và fail-closed tests xanh.

### 7.7 — Security, regression và evidence

- IDOR/fuzz/budget/drop tests;
- hosted CI/core tests;
- controlled Windows R25 build/smoke;
- evidence report;
- GO/NO-GO review.

**Exit:** Definition of Done đạt và không có NO-GO blocker.

## 17. File-level change plan

### Shared contracts

- `packages/contracts/src/autocad_contracts/agent_protocol.py`
- `packages/contracts/src/autocad_contracts/runtime.py`
- new CAD Program Python models/canonicalization module
- protocol/runtime/program tests and golden vectors

### Host contracts

- new `packages/host_contracts/schemas/cad-program-0.2.schema.json`
- program request/result schemas
- preview/commit/validate golden envelopes
- invalid corpus and cross-language digest vectors

### Gateway

- `services/gateway/src/autocad_gateway/app.py`
- `services/gateway/src/autocad_gateway/contracts.py`
- `services/gateway/src/autocad_gateway/services.py` only as compatibility facade if still needed
- `services/gateway/src/autocad_gateway/application/` program service/policy/materializer
- `services/gateway/src/autocad_gateway/domain/` program/preview models
- `services/gateway/src/autocad_gateway/infrastructure/sqlite/` migrations/repositories
- Gateway tool, repository, job, resource, OAuth and isolation tests

### Desktop Agent

- `apps/desktop_agent/src/autocad_desktop_agent/runtime/contracts.py`
- `runtime/broker.py`
- `runtime/managed_dotnet.py`
- `runtime/autolisp_file_ipc.py` only for explicit portable support
- `executor.py` or new typed program executor
- `ledger.py`
- `core.py`
- `state.py`
- `ui/window.py`
- Agent protocol, ledger, runtime, UI and reconnect tests

### Managed Host

- `AutocadMcp.Host.Core/CadProgram.cs` refactor/version split
- operation registry/hash module
- R25 program operations
- durable receipt/ledger
- protocol payload validation
- Host Core tests
- real AutoCAD lab scripts/evidence

### Documentation

- update `Phase-6-plus.md` status/link after implementation starts;
- update UI appendix only for implemented UI behavior;
- add Phase 7 evidence, runbook, limitation matrix và GO/NO-GO report.

## 18. Test matrix

### 18.1. Contract/schema

- valid/invalid 0.2 corpus;
- unknown fields;
- duplicate operation ID;
- forward output ref;
- type mismatch ref;
- non-finite/oversized geometry;
- Unicode text;
- canonical hash Python/C#;
- registry/policy/runtime binding invalidation.

### 18.2. Gateway domain/repository

- owner isolation;
- program revision immutability;
- prepare idempotency;
- preview/commit idempotency separation;
- snapshot/device/document mismatch;
- preview expiry;
- atomic job + preview/commit materialization;
- duplicate terminal result;
- conflicting duplicate;
- migration checksum and restart.

### 18.3. Public MCP contract

- exact four tool schemas;
- OAuth `autocad.write` required;
- correct annotations;
- safe errors;
- resource links owner-scoped;
- old read tools unchanged;
- write tools absent/disabled under kill switch.

### 18.4. Agent

- capability missing before ACK;
- paused/write-disabled rejection;
- runtime/package/registry mismatch;
- no silent fallback;
- ledger accepted/started/terminal;
- duplicate exact payload;
- duplicate conflict;
- Agent restart before start and after terminal;
- revoked/replaced session result rejected.

### 18.5. Managed Host Core

- parser/property/fuzz;
- operation registry/hash;
- output ref resolver;
- execution digest;
- receipt conflict;
- bounded result;
- no arbitrary operation/path/assembly.

### 18.6. AutoCAD Mechanical 2025 real

- no active document;
- busy/modal;
- document switch;
- stale revision;
- preview abort and unchanged revision;
- line/circle/polyline/rectangle/text/dimension;
- commit and revision advance;
- postcondition validation;
- immediate duplicate;
- Agent disconnect;
- Host/AutoCAD restart + DWG reopen duplicate;
- package/runtime change invalidates preview;
- hard pause.

### 18.7. LT

- read regression always;
- write capability absent by default;
- no Managed Host load;
- portable tests only when implementation/evidence exists.

### 18.8. Security

- IDOR across program/preview/job/resource;
- forged runtime/package/capability binding;
- payload hash mismatch;
- oversized/deep JSON;
- path/command/LISP/C#/DLL injection corpus;
- replay/expired preview;
- owner/session swap;
- log/diagnostics redaction.

## 19. CI and real-runtime policy

Per PR:

- Python unit/integration tests;
- Host Core tests without Autodesk assemblies;
- JSON schema/golden validation;
- formatting/static checks;
- migration snapshot/checksum;
- LT legacy regression;
- package XML/policy lint.

Controlled Windows builder:

- R25 compile against approved Autodesk references;
- deterministic package/version/hash;
- signing where available;
- install into trusted location;
- real Mechanical 2025 smoke;
- publish evidence only after operator approval.

GitHub hosted CI không tự phát hành production DLL/installer hoặc chứa signing secret.

## 20. Definition of Done

Phase 7 đạt khi tất cả mục sau đúng:

1. `cad.program/0.2` strict schema và cross-language canonical digest xanh.
2. Program/preview records owner-scoped và durable qua Gateway restart.
3. `cad_prepare_program` không cho model tự chọn runtime/package/policy binding.
4. `cad_preview` đi public Gateway → Agent → Managed Host → Mechanical 2025 thật.
5. Preview transaction abort và document revision không đổi.
6. `cad_commit` chỉ nhận exact unexpired preview.
7. Program patch/revision mới invalid preview cũ.
8. Document revision đổi invalid preview/commit.
9. Runtime/package/capability/registry/policy đổi invalid preview.
10. Create-only primitives bắt buộc chạy trên R25.
11. Output refs chỉ trỏ step trước và được type-check.
12. Postconditions bounded được validate.
13. Exact duplicate không tạo effect lần hai.
14. Conflicting duplicate bị reject.
15. Agent/Host/AutoCAD restart + DWG reopen vẫn reconcile durable receipt cho test case đã chứng minh.
16. Unknown started write không tự retry.
17. Managed write và LT write có kill switch độc lập.
18. LT không công bố write capability nếu chưa real-certified.
19. Read tools và LT compatibility path không regression.
20. `autocad.write` scope bắt buộc cho public write surface.
21. IDOR tests phủ program, preview, job, result và resource.
22. Hard pause/local write lock chặn trước Host.
23. Không arbitrary code/path/assembly/command/network capability mới.
24. Public errors/logs/diagnostics không rò secret hoặc drawing content.
25. Tool annotations phản ánh đúng write side effects.
26. Real demo tấm chữ nhật có bốn lỗ, text/dimension tối thiểu preview → commit → validate thành công trên Mechanical 2025.
27. Evidence report ghi test counts, commit SHA, package hash, DWG fixture và operator result.
28. Phase 6 isolation gate đạt trước khi gắn nhãn multi-user/customer-ready.

## 21. NO-GO criteria

NO-GO nếu có một trong các điều sau:

- commit có thể chạy mà không có exact preview;
- model/client tự cung cấp trusted runtime/package/policy binding;
- runtime đổi nhưng commit vẫn tiếp tục;
- preview làm thay đổi drawing;
- duplicate có thể tạo entity lần hai;
- write started bị auto retry khi outcome chưa biết;
- owner A đọc/chạy program hoặc preview của owner B;
- revoked/replaced session vẫn gửi terminal result hợp lệ;
- Agent silent fallback write sang LT/LISP;
- LT write bật khi chưa có real evidence;
- raw command/LISP/C#/DLL/path injection lọt qua;
- global/runtime kill switch fail-open;
- package/capability/registry mismatch không bị chặn;
- result/job/program materialization không atomic;
- public logs/errors chứa drawing content, secret hoặc stack trace;
- read-only Phase 4/5 regression hỏng;
- Phase 6 tenant isolation chưa đạt nhưng release được mô tả là customer-ready.

## 22. Rollback

Rollback phải giữ observe/query hoạt động:

1. đặt `AUTOCAD_MCP_LOW_RISK_COMMIT_ENABLED=0`;
2. đặt `AUTOCAD_MCP_MANAGED_WRITE_ENABLED=0` và `AUTOCAD_MCP_LT_WRITE_ENABLED=0`;
3. ẩn/disable four public write tools theo policy;
4. không xóa program/preview/job audit records;
5. Agent trở về read-only runtime broker;
6. Managed Host giữ read operations và có thể bỏ load write registry bằng policy/package rollback;
7. không rollback migration destructive;
8. preserve old Phase 5 POC fixtures để điều tra nhưng không expose public.

## 23. Demo nghiệm thu

Demo chuẩn:

1. User đã pair đúng Full/.NET device.
2. `cad_observe` tạo snapshot/revision.
3. ChatGPT prepare program tạo một tấm chữ nhật, bốn lỗ, layer, text và linear dimension.
4. Gateway trả program revision/digest và capability summary.
5. Preview chạy qua Managed .NET, transaction abort, drawing không đổi.
6. Commit exact preview tạo geometry một lần.
7. Validate xác nhận counts/types/bounds và revision mới.
8. Gọi lại exact commit trả duplicate, drawing không tăng entity.
9. Thay package/runtime hoặc sửa drawing sau preview làm commit bị từ chối.
10. User khác không list/read/commit được program, preview, job hoặc result này.

## 24. Deliverables

- CAD Program 0.2 schemas và golden vectors.
- Gateway program/preview persistence và migrations.
- Four public tools + owner-scoped resources.
- Agent typed write executor, runtime broker policy và durable ledger extension.
- Managed Host 0.2 registry/primitives/preview/commit/validate.
- LT limitation hoặc certification evidence.
- Unit/integration/security/real AutoCAD test matrix.
- Phase 7 runbook, evidence report và GO/NO-GO checklist.

## 25. Quyết định hoãn sang Phase 8+

- trusted approval/consent record và companion approval UI;
- full disconnect/crash drop matrix;
- public rollback/checkpoint restore;
- manual operator recovery workflow;
- modify/delete/pattern/block operations;
- skill/workflow orchestration;
- Scene Graph;
- broad customer rollout và production SLO.

---

Phase 7 hoàn thành khi CAD Program create-only không còn là một Host POC riêng lẻ mà trở thành public, owner-scoped, runtime-pinned, policy-gated và durable write path. Tuy vậy, release vẫn phải mô tả đúng giới hạn: low-risk create-only, Managed .NET primary, LT fail closed nếu chưa certified, và recovery/approval hoàn chỉnh tiếp tục thuộc Phase 8.
