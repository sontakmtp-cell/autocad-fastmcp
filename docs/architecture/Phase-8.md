# Phase 8 — CAD Program v1 and Cross-Runtime Capability

> Trạng thái: kế hoạch kiến trúc và triển khai.
>
> Baseline: sau [Phase-6.md](./Phase-6.md) và [Phase-7.md](./Phase-7.md) đạt exit gate.
>
> Phase 8 mở rộng CAD capability nhưng giữ public MCP surface nhỏ, capability-aware và runtime-neutral.

## 1. Executive summary

Phase 6 cung cấp create-only CAD Program v0. Phase 7 cung cấp recovery, trusted approval và rollback. Phase 8 mở CAD Program v1 để ChatGPT xử lý các bản vẽ đa dạng hơn mà không cần tạo một public tool cho mỗi AutoCAD command.

```text
ChatGPT
→ observes immutable snapshot/scene summary
→ creates or patches CAD Program v1
→ Gateway validates capability/risk/budget
→ preview + trusted approval when required
→ Agent pins runtime
→ runtime adapter executes exact operation registry
→ validate/reconcile/rollback
```

Managed .NET tiếp tục là primary path cho AutoCAD Full. LT chỉ chạy subset đã có conformance evidence. Unsupported operation fail closed, không silently approximate hoặc đổi runtime sau preview.

## 2. Mục tiêu

1. Tạo `cad.program/1.0` versioned, bounded và cross-language.
2. Hỗ trợ variables, safe expressions và bounded repeat/pattern.
3. Hỗ trợ common modify operations và scoped delete.
4. Hỗ trợ snapshot/query references an toàn.
5. Mở block, attributes, annotation và dimension có kiểm soát.
6. Tạo program patch/rebase lifecycle.
7. Giữ public tools ổn định; primitive không thành MCP tool.
8. Enforce capability tiers và runtime-specific support.
9. Reuse Phase 7 approval/recovery/rollback cho risk cao.
10. Xây conformance suite giữa Managed .NET, LT compatibility và ezdxf nơi semantic tương đương.

## 3. Non-goals

- arbitrary AutoCAD command, LISP, C#, DLL, shell hoặc Python;
- unbounded loops/recursion;
- generic scripting language;
- automatic feature inference authority;
- full parametric constraint solver;
- organization/team sharing;
- marketplace hoặc arbitrary third-party plugins;
- production scale migration;
- silent downgrade/fallback;
- promise parity across all runtimes/releases.

## 4. Contract `cad.program/1.0`

### 4.1. Core structure

Program includes:

- program ID/revision/schema version;
- source snapshot/document identity/revision;
- variables and typed values;
- ordered statements/operations;
- safe expression AST;
- references;
- preconditions/postconditions;
- budgets;
- required capability tiers;
- risk summary;
- semantic digest.

Execution binding remains Gateway-generated and includes runtime/package/capability/registry/policy/execution digest.

### 4.2. Safe expressions

Allowed:

- finite numeric literals;
- variables;
- `+ - * /` with divide-by-zero checks;
- bounded min/max/abs;
- angle conversion;
- point/vector construction;
- polar coordinate helper;
- explicit unit conversion within configured units.

Forbidden:

- eval/reflection;
- string-to-code;
- file/network/environment access;
- time/randomness unless deterministic seeded operation is explicitly defined;
- recursion;
- dynamic function lookup.

AST depth/node count and numeric magnitude are bounded.

### 4.3. Bounded repeat/pattern

- repeat count statically bounded;
- expanded entity/operation budget estimated before dispatch;
- no while loop;
- loop variable immutable per iteration;
- nested repeat either forbidden in v1.0 or limited to depth 2 with total expansion ceiling;
- deterministic expansion digest.

Patterns:

- rectangular;
- polar;
- linear;
- path pattern deferred unless semantics/evidence are strong enough.

### 4.4. Snapshot/query references

References may target only:

- immutable owner-scoped snapshot;
- typed query result set with bounded pagination materialized before prepare;
- outputs from prior operations;
- reusable component definitions pinned by version/digest.

Raw handle/ObjectId alone is not sufficient. Reference binding includes document/snapshot/revision and optional geometry fingerprint.

## 5. Operation set

### 5.1. Create and transform

- all v0 operations;
- copy;
- move;
- rotate;
- scale;
- mirror;
- rectangular/polar pattern.

### 5.2. Geometry modification

- offset;
- fillet;
- chamfer;
- trim/extend only after deterministic target/cutter semantics and real failure evidence;
- join/explode deferred or high-risk depending object type.

### 5.3. Delete/erase

- exact scoped entity refs only;
- immutable source snapshot required;
- explicit maximum count;
- high-risk floor for broad selection;
- trusted approval according to Phase 7;
- rollback plan required where supported;
- purge remains deferred or admin-only packaged workflow.

### 5.4. Blocks and attributes

- insert existing allowlisted block definition;
- create reusable component from bounded program output if policy permits;
- set typed attributes;
- no arbitrary external DWG/path import;
- component ref pinned by digest/version.

### 5.5. Annotation

- text/mtext bounded;
- linear/aligned/angular/radial/diameter dimensions where runtime supports;
- center marks/centerlines;
- leaders within bounded templates;
- style refs must be allowlisted or ensured through safe operation.

## 6. Capability tiers

| Tier | Meaning |
|---|---|
| `portable_core` | Equivalent semantic proven across selected .NET/LT/headless implementations |
| `managed_standard` | General AutoCAD Full Managed .NET capability |
| `managed_advanced` | Release/vertical-specific Managed .NET capability |
| `lt_compat` | Packaged AutoLISP/File IPC compatibility operation |
| `headless_only` | DXF/offline/test capability |
| `experimental` | Lab allowlist only |

Rules:

- skill/program declare required capabilities, not edition names unless truly necessary;
- prepare checks current manifest;
- Agent and Host check again before execution;
- unsupported op returns `capability_missing`;
- registry/compiler/runtime changes invalidate preview/approval;
- no fallback for write after preview.

## 7. Program patch and rebase

### Patch

Patch creates a new immutable program revision. It never mutates prior revision.

Patch operations may:

- add/remove/replace program operations;
- update variables/postconditions/budgets within policy;
- rebind selected snapshot refs only through explicit rebase.

Old preview/consent becomes invalid.

### Rebase

Rebase compares original snapshot references to a new snapshot:

- exact unchanged refs can carry forward;
- moved/changed/missing refs produce conflict report;
- no automatic semantic merge for destructive operations;
- user/model may create a new patch after inspecting conflicts;
- new program and execution digests generated.

## 8. Risk model

Risk is maximum of:

- operation floor;
- selected entity count/scope;
- document/runtime/revision strength;
- external-effect classification;
- tenant/device policy;
- rollback confidence.

Examples:

- create line/circle: low;
- move/copy bounded exact refs: low/medium;
- trim/fillet exact refs: medium;
- delete exact small set: medium/high;
- broad delete/purge: high or unsupported;
- external file/import/command: unsupported in v1.

Phase 7 trusted approval levels apply without weakening.

## 9. Runtime implementations

### Managed .NET

- direct database/API handlers;
- document lock and transactions;
- database transaction abort preview for supported operations;
- explicit object-type adapters;
- event/revision evidence;
- operation-specific validation and rollback planning.

### AutoLISP/File IPC

- only packaged/allowlisted compiler targets;
- capability manifest advertises tested subset;
- no arbitrary generated LISP unless dedicated compiler security review and conformance gate exist;
- conservative preview/recovery;
- LT write stays off until real LT certification.

### ezdxf

- deterministic offline/headless implementation where semantic fits;
- golden geometry and contract tests;
- result marked non-authoritative for live DWG commit;
- no claim of vertical/custom object parity.

## 10. Conformance suite

For each `portable_core` operation:

- identical semantic input;
- normalized result contract;
- equivalent geometry within declared tolerance;
- deterministic digest/expansion;
- same error category for invalid input;
- runtime-specific evidence allowed;
- unsupported object type fail closed;
- preview/commit validation semantics documented.

Fixtures:

- basic 2D geometry;
- units/angles;
- patterns;
- layers/styles;
- block insert;
- annotations;
- snapshot refs;
- stale/conflict cases;
- duplicate/recovery cases.

Portable status is earned per operation/version/runtime family, not inherited by name.

## 11. Gateway and public MCP

Keep high-level tools:

- `cad_prepare_program`;
- `cad_preview`;
- `cad_commit`;
- `cad_validate`;
- `cad_get_job`;
- rollback resources/tools from Phase 7;
- query/observe resources.

Do not add `cad_move`, `cad_rotate`, `cad_delete`, etc.

Public schema may reference versioned CAD Program resource or bounded typed program payload. Large programs/artifacts use resource references rather than huge tool results.

## 12. Storage

Additive records/fields:

- program schema version;
- variable/expression AST digest;
- component refs;
- snapshot query materializations;
- patch/rebase lineage;
- conflict reports;
- conformance/runtime support metadata;
- operation-level validation evidence.

All immutable revisions and owner-scoped.

## 13. Desktop Agent and Host

Agent:

- capability-aware admission;
- expansion/budget check against Gateway plan;
- per-document serialization;
- runtime/registry pinning;
- Phase 7 evidence/recovery reuse;
- no semantic fallback.

Host:

- explicit operation registry;
- no reflection dispatch;
- typed handlers;
- operation-specific preview/commit/validate/rollback strategies;
- object-type allowlist;
- bounded result/evidence;
- registry hash and version.

## 14. UI

ChatGPT remains primary authoring interface.

Agent/Portal show:

- required vs available capability;
- program version/revision;
- estimated operation/entity counts;
- risk and approval requirement;
- runtime-specific unsupported operations;
- patch/rebase conflict;
- exact preview invalidation reason;
- operation pack/registry version.

Do not expose raw primitive toggles to ordinary users or let them force unsupported runtime.

## 15. Feature flags

```text
AUTOCAD_MCP_PROGRAM_V1_ENABLED=0
AUTOCAD_MCP_MANAGED_STANDARD_ENABLED=0
AUTOCAD_MCP_MANAGED_ADVANCED_ENABLED=0
AUTOCAD_MCP_LT_PORTABLE_WRITE_ENABLED=0
AUTOCAD_MCP_DESTRUCTIVE_OPERATIONS_ENABLED=0
AUTOCAD_MCP_OPERATION_PACK_ALLOWLIST=
```

Rollout per operation pack/runtime family/cohort.

## 16. Test matrix

### Contract/expression

- strict AST;
- depth/node/numeric limits;
- deterministic expansion;
- divide-by-zero/non-finite rejection;
- invalid refs and forward refs;
- patch/rebase lineage;
- digest parity Python/C#.

### Geometry

- move/copy/rotate/scale/mirror;
- patterns and budget expansion;
- offset/fillet/chamfer edge cases;
- annotation/style refs;
- blocks/attributes;
- tolerance and units.

### Risk/security

- scoped delete;
- broad selection rejection;
- approval floor;
- runtime/capability mismatch;
- arbitrary path/code rejection;
- old preview/consent invalid after patch/rebase.

### Recovery

- all Phase 7 drop/idempotency cases for new operations;
- rollback conflicts;
- registry update mid-lifecycle;
- runtime unavailable/replacement.

### Cross-runtime

- portable conformance fixtures;
- LT unsupported cases;
- ezdxf non-authoritative markers;
- no silent approximation.

## 17. Milestones

1. Contract/AST and no-op evaluator.
2. Variables + expressions + transform operations on .NET.
3. Bounded patterns and conformance fixtures.
4. Snapshot refs and patch/rebase.
5. Blocks/annotation.
6. Scoped delete and high-risk approval integration.
7. LT portable subset certification if real environment available.
8. Live E2E and operation-pack rollout evidence.

## 18. Exit criteria

- `cad.program/1.0` strict and digest-parity;
- variables/expressions/patterns bounded and deterministic;
- transform/modify operation set runs on Mechanical 2025 .NET;
- snapshot refs stale/conflict safe;
- patch/rebase invalidates old preview/consent;
- destructive operations use Phase 7 trusted approval and rollback policy;
- no new public tool per primitive;
- unsupported runtime fails `capability_missing`;
- portable claims backed by conformance evidence;
- LT/read paths do not regression;
- arbitrary code/path/command remains impossible.

## 19. Rollback

- disable v1 and operation packs;
- retain v0 public create-only path;
- keep program revisions/evidence for audit;
- invalidate outstanding v1 previews/consents when pack disabled;
- no downgrade execution of v1 program as v0;
- return affected cohort to read-only when registry safety is uncertain.

## 20. Definition of Done

Phase 8 hoàn tất khi ChatGPT có thể tạo và sửa một bounded CAD Program v1 dùng variables, patterns và common modify operations trên Managed .NET; risk/approval/recovery vẫn giữ nguyên Phase 7 guarantees, public tool set không nổ tung, và mọi cross-runtime support claim có capability/conformance evidence rõ ràng.
