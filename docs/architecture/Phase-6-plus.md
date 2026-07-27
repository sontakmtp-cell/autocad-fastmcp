# Roadmap triển khai Phase 6–12 — AutoCAD MCP đa runtime

> Trạng thái cập nhật 2026-07-27: Phase 6 đã merge vào `main` qua PR #10 tại commit
> `6edb9d9fb06b7e8565436b90e6891be80da0c7e0`.
>
> Phase 6 đạt **Engineering GO cho local integration** và vẫn **Customer Pilot NO-GO** do các gate production bên ngoài chưa hoàn tất.
>
> Phase hiện hành là **Phase 7 — Durable Recovery, Trusted Approval and Conflict-Safe Rollback**.
>
> Tài liệu chi tiết:
>
> - [Phase-6.md](./Phase-6.md)
> - [phase6-public-cad-program-evidence.md](./phase6-public-cad-program-evidence.md)
> - [Phase-7.md](./Phase-7.md)
> - [Phase-8.md](./Phase-8.md)
> - [appendix-user-interface.md](./appendix-user-interface.md)

## 1. Baseline sau Phase 6

Đã có trên public/product path:

- FastMCP public read surface;
- durable Gateway job state machine;
- outbound WSS Desktop Agent;
- Managed .NET Host R25 và RuntimeBroker;
- AutoLISP/File IPC compatibility path;
- production identity/pairing và owner isolation;
- `cad.program/0.2` create-only strict;
- public owner-scoped `prepare → preview → commit → validate`;
- exact runtime/package/capability/registry/policy binding;
- preview transaction abort;
- commit effect và durable DWG receipt trong cùng transaction;
- exact duplicate không tạo effect thứ hai;
- `outcome_unknown` giữ write lock và exact receipt reconciliation;
- Portal observation-only cho program/preview/job/receipt/validation;
- live Mechanical 2025 evidence;
- write default-off và LT read compatibility không regression;
- R25 lab packaging/rollback engineering và telemetry fail-open pilot.

Chưa có:

- immutable execution intent và trusted consent;
- Agent local confirmation;
- Portal recent-auth approval;
- operator-facing RecoveryCase workflow;
- Phase-7 geometry checkpoint;
- public conflict-safe rollback;
- full automated/live fault-injection matrix;
- CAD Program v1 modify/delete/pattern;
- skill/workflow platform;
- Scene Graph;
- full customer installer/update ecosystem;
- production scale/SLO/ecosystem.

External Customer Pilot gates vẫn còn:

- CA-issued code-signing certificate và trusted timestamp;
- private-key custody/provenance/SBOM/malware approval;
- public OAuth lifecycle run trên pilot tenant;
- live revoke/re-pair drill;
- modal/busy và unknown-outcome drill trên pilot device;
- telemetry soak 3–7 ngày;
- support ownership và explicit pilot cohort approval.

## 2. Numbering hiện hành

| Phase | Tên | Kết quả chính | Trạng thái |
|---:|---|---|---|
| 5 | Runtime, Identity and Local Write Foundation | .NET R25 + LT compatibility + pairing/isolation + local write POC | Merged |
| 6 | Public CAD Program v0 and Managed Write Pilot | Public create-only prepare/preview/commit/validate | Engineering complete |
| 7 | Durable Recovery, Trusted Approval and Conflict-Safe Rollback | Intent, consent, recovery case, checkpoint và rollback | Current |
| 8 | CAD Program v1 and Cross-Runtime Capability | Variables, pattern, modify/delete, annotation và capability tiers | Planned |
| 9 | Skill and Workflow Platform | Skill catalog, durable workflows, pause/resume/approval/recovery | Planned |
| 10 | Scene Graph and Drawing Intelligence | Relation graph, contour/feature inference, validation nâng cao | Planned |
| 11 | Packaging, Distribution and Multi-User Pilot | Installer, signed releases, update/rollback và customer pilot | Planned |
| 12 | Production Hardening, Scale and Ecosystem | SLO, quota, backup/restore, scale và ecosystem | Planned |

Roadmap cũ đặt identity ở Phase 6 và kết thúc ở Phase 13. Identity đã được hấp thụ vào Phase 5.6, vì vậy numbering trên là nguồn hiện hành.

## 3. Nguyên tắc thứ tự

1. Không mở public write trước owner isolation và runtime evidence.
2. Không mở broad CAD Program trước create-only preview/commit/validate.
3. Không cho model tự approve hoặc phát approval proof.
4. Không auto retry write đã started hoặc chưa chứng minh not-started.
5. Không mở modify/delete trước trusted approval, recovery và rollback.
6. Không coi bounds-only receipt cũ là rollback checkpoint mạnh.
7. Không xây skill/workflow trước khi CAD Program contract ổn định.
8. Không xây Scene Graph lớn trước snapshot/revision/entity model đủ tin cậy.
9. Mọi phase thay runtime contract phải chạy LT compatibility regression.
10. Không tuyên bố support release chỉ vì compile; cần real load/smoke.
11. AutoCAD Full không bị hạ xuống giới hạn của AutoCAD LT.

---

# Phase 6 — Public CAD Program v0 and Managed Write Pilot

## Kết quả

Phase 6 đã productize local create-only POC thành public owner-scoped flow:

```text
Observe → Prepare → Preview → Commit → Validate
```

Đã triển khai:

- `cad.program/0.2` runtime-neutral;
- create-only operation registry;
- public FastMCP tools/resources;
- owner-scoped program/preview/validation/receipt storage;
- typed Gateway–Agent commands;
- Agent ProgramCommandExecutor qua RuntimeBroker;
- Managed .NET R25 primary write path;
- runtime/package/capability/registry/policy pinning;
- write lock, hard pause, kill switches và allowlist;
- retained unknown commit lock + exact receipt reconciliation;
- live Mechanical 2025 E2E.

## Exit status

Engineering exit đã đạt:

- preview không đổi DWG;
- commit tạo effect một lần;
- exact duplicate không duplicate;
- stale document/runtime/package/policy bị chặn;
- cross-owner access `not_found`;
- hard pause/write lock chặn trước Host;
- started/unknown write không blind retry;
- LT vẫn read-only và regression xanh.

Customer Pilot vẫn NO-GO vì external production gates.

Chi tiết và evidence:

- [Phase-6.md](./Phase-6.md)
- [phase6-public-cad-program-evidence.md](./phase6-public-cad-program-evidence.md)

---

# Phase 7 — Durable Recovery, Trusted Approval and Conflict-Safe Rollback

## Mục tiêu

Bọc existing Phase 6 create-only path bằng C2 control envelope mà không xây lại durable jobs hoặc receipt reconciliation.

```text
cad_commit
→ immutable intent
→ trusted approval when required
→ atomic consent consume + one job release
→ existing durable execution/reconcile
→ RecoveryCase when unresolved
→ checkpointed rollback when conflict-free
```

## Phạm vi

- giữ public tool `cad_commit`, không tạo `cad_request_commit` mới;
- immutable execution intent;
- one-time owner-scoped consent;
- Agent local confirmation;
- Portal recent-auth approval;
- atomic consent consume, job create/reuse và intent release;
- append-only execution evidence trên existing job state machine;
- extended fault/drop matrix;
- operator-facing `outcome_unknown` RecoveryCase;
- Phase-7 checkpoint committed cùng new effect/receipt;
- `cad_preview_rollback` và `cad_commit_rollback`;
- strict revision/fingerprint/dependency conflict detection;
- hard pause, revoke và kill switches xuyên intent/consent/job/recovery lifecycle;
- operator diagnostics và safe evidence refresh.

## Quan trọng

- Phase 6 đã có unknown-outcome lock retention và exact receipt reconciliation; Phase 7 mở rộng, không thay thế.
- Effect và receipt đang commit cùng AutoCAD transaction; không mô hình hóa durable “effect-only” state.
- Existing Phase 6 receipt v2 mặc định `rollback_eligible=false` vì chưa có geometry fingerprint/provenance đủ mạnh.
- Chỉ commit mới có `cad.rollback.checkpoint/1` mới được public rollback.
- Rollback v0 chỉ xóa checkpoint-owned created entities; không xóa layer/style/block/shared object.
- Strict rollback yêu cầu exact current document revision; scoped rebase để Phase 8.
- Approval không bao giờ là MCP tool hoặc `confirm=true`.

## Exit

- model không thể approve hoặc hạ assurance;
- exact `cad_commit` retry trả cùng intent/job/receipt;
- một consumed consent release tối đa một job;
- mọi tested drop point không duplicate effect;
- exact outcome được chứng minh hoặc giữ safely unknown với RecoveryCase;
- recovery query không re-execute;
- rollback chỉ chạy với Phase-7 checkpoint, strict conflict-free và trusted approval;
- duplicate rollback không tạo effect thứ hai;
- old receipts không bị quảng cáo sai là rollbackable;
- owner isolation, hard pause, revoke và redaction xanh;
- live Mechanical 2025 evidence hoàn tất.

## Chưa làm

Broad CAD operations, rollback rebase sau unrelated edits, layer/shared-object cleanup, team sharing, skill workflows, Scene Graph, LT write và production scale.

Chi tiết: [Phase-7.md](./Phase-7.md).

---

# Phase 8 — CAD Program v1 and Cross-Runtime Capability

## Mục tiêu

Mở khả năng CAD tổng quát hơn mà không biến public MCP thành hàng trăm primitive tools và không kéo AutoCAD Full xuống giới hạn LT.

## Năng lực

- variables và safe expressions;
- bounded repeat/pattern;
- immutable snapshot/query references;
- move/copy/rotate/scale/mirror;
- offset/fillet/chamfer;
- carefully gated trim/extend;
- block insert/attributes;
- annotation/dimension mở rộng;
- erase/delete có risk floor;
- program patch/rebase;
- reusable component refs;
- validation profiles;
- rollback conflict scope/rebase khi evidence đủ mạnh.

## Capability tiers

- `portable_core`;
- `managed_standard`;
- `managed_advanced`;
- `lt_compat`;
- `headless_only`;
- `experimental`.

## Exit

- v1 program chạy trên Full/.NET;
- unsupported operation fail `capability_missing`;
- destructive/high-risk operations bắt buộc dùng Phase 7 trusted approval;
- preview invalid khi registry/compiler/runtime thay đổi;
- portable subset có conformance evidence trước khi mở LT;
- patch/rebase không bypass document revision và checkpoint policy.

Chi tiết: [Phase-8.md](./Phase-8.md).

---

# Phase 9 — Skill and Workflow Platform

## Mục tiêu

Biến CAD Program và primitive thành nền cho reusable engineering workflows mà không khóa ChatGPT vào macro cứng.

## Phạm vi

- versioned skill catalog;
- skill metadata, input schema, required capability và risk;
- CAD Program templates và validation profiles;
- workflow graph với pause/resume/retry/patch;
- waiting-for-user và trusted approval states;
- durable workflow execution;
- compatibility/support matrix theo runtime/release;
- skill publication, rollback và deprecation.

## Nguyên tắc

- skill là kiến thức và template, không phải public tool riêng;
- ChatGPT vẫn có thể tạo CAD Program tự do;
- workflow không bypass Gateway policy hoặc Phase 7 consent;
- không arbitrary third-party code.

## Exit

- ít nhất ba workflows thật: auto-dimension, drawing cleanup và một domain workflow;
- restart/reconnect không mất workflow state;
- skill version pin vào execution/audit;
- unsupported runtime được báo rõ.

---

# Phase 10 — Scene Graph and Drawing Intelligence

## Mục tiêu

Cung cấp drawing model có ý nghĩa hơn entity list để ChatGPT quan sát, lập kế hoạch, tự sửa và validate.

## Lộ trình

1. normalized entity snapshot;
2. spatial index và relation graph;
3. contour/region và annotation links;
4. feature inference: hole, slot, centerline, part;
5. anomaly/error detection.

Relations ưu tiên:

- inside/intersect/touch;
- parallel/perpendicular;
- concentric/aligned/symmetric;
- connected/overlap.

## Exit

- bounded/paginated scene resources;
- confidence/evidence cho inferred features;
- stable relation IDs trong snapshot scope;
- scene data không làm context nổ tung;
- inference không được dùng làm destructive authority nếu chưa revalidate.

---

# Phase 11 — Packaging, Distribution and Multi-User Pilot

## Mục tiêu

Biến engineering artifacts thành product bundle có thể cài, cập nhật, rollback và hỗ trợ cho pilot users.

## Phạm vi

- CA-signed Agent/Host/installers;
- trusted timestamp;
- release manifest, SBOM và provenance;
- runtime-aware installer/probe;
- R22/R23/R24/R25 release families theo evidence;
- LT 2024+ real certification;
- Portal download/update pages;
- staged rollout, minimum/recommended versions;
- previous-known-good package rollback;
- diagnostics/support bundles;
- onboarding không yêu cầu `.env`, terminal, port hoặc tunnel;
- limited customer pilot.

## Exit

- clean install/upgrade/rollback trên clean VMs;
- exact family component selection;
- LT không load Managed Host;
- signed artifact validation fail closed;
- Phase 7 C2 controls chạy trên pilot cohort;
- pilot runbook, support ownership và incident rollback;
- customer cohort evidence.

---

# Phase 12 — Production Hardening, Scale and Ecosystem

## Mục tiêu

Đưa hệ thống từ pilot sang production nhiều user/device và có đường phát hành capability/skill an toàn.

## Phạm vi

- SLO/SLI, alerting và incident response;
- quota/rate limits/subscription hooks;
- backup/restore và retention;
- PostgreSQL/queue/multi-worker khi load evidence yêu cầu;
- connection/device/job scale tests;
- tenant-aware admin/support;
- staged Agent/Host/registry/skill rollout;
- compatibility manifest và forced minimum version;
- audit export và privacy controls;
- ecosystem governance cho signed skills/capability packs.

## Exit

- production SLO và capacity envelope;
- disaster recovery drill;
- rolling deploy không mất durable truth;
- tenant isolation test ở scale;
- signed publication/revoke/rollback cho skills và operation packs;
- no arbitrary code marketplace.

## 4. Cross-phase gates

### Security

- OAuth/owner/device/session checks trước dispatch;
- Agent/Host validate lại binding;
- arbitrary code/path/network off;
- risk floor không thể bị UI/model hạ;
- approval chỉ qua trusted channel;
- replay/idempotency/CAS tests;
- audit correlation end-to-end;
- checkpoint provenance trước rollback.

### Runtime

- Managed .NET primary cho Full;
- LT compatibility không regression;
- no silent fallback cho write/preview/rollback;
- ezdxf non-authoritative cho live DWG safety;
- release support chỉ sau real evidence.

### Operations

- kill switches riêng cho managed write, trusted approval, rollback, LT write và operation packs;
- telemetry fail open;
- no update giữa active effect-bearing job;
- previous-known-good package rollback;
- external production signing gates rõ ràng;
- unresolved outcome records không được xóa hoặc tự động đánh success.

## 5. Tài liệu ưu tiên

Khi mâu thuẫn:

1. [fastmcp-multi-user-autocad-plan.md](./fastmcp-multi-user-autocad-plan.md) quyết định architecture boundaries.
2. Tài liệu phase cụ thể quyết định scope, contract và exit gate.
3. Tài liệu này quyết định numbering và thứ tự Phase 6–12.
4. [appendix-user-interface.md](./appendix-user-interface.md) quyết định cách render state và trusted actions.
5. Evidence documents quyết định điều gì đã thực sự được chứng minh.

Phase hiện hành: [Phase-7.md](./Phase-7.md).