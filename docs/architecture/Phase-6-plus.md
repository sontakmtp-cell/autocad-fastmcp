# Roadmap triển khai Phase 6–12 — AutoCAD MCP đa runtime

> Trạng thái cập nhật 2026-07-26: PR #8 đã merge vào `main` tại commit `a3ddacc5e45fa2a3dbf1966ed2d35f12d04a55a7`.
>
> Phase 5 đã hấp thụ runtime foundation, production identity/pairing, owner isolation, Portal tối thiểu, local CAD Program POC, R25 packaging/signing/rollback engineering và telemetry pilot. Vì vậy phase numbering sau Phase 5 được chuẩn hóa lại.
>
> Đây là roadmap sản phẩm. Kế hoạch chi tiết hiện tại nằm trong [Phase-6.md](./Phase-6.md), [Phase-7.md](./Phase-7.md), [Phase-8.md](./Phase-8.md) và [appendix-user-interface.md](./appendix-user-interface.md).

## 1. Baseline sau PR #8

Đã có:

- FastMCP public read surface;
- durable Gateway job state machine;
- outbound WSS Desktop Agent;
- Managed .NET Host R25 và RuntimeBroker;
- AutoLISP/File IPC compatibility path;
- create-only CAD Program local POC;
- browser pairing, device ownership và two-user/two-device pilot;
- Portal tối thiểu;
- signed R25 lab package, clean-VM upgrade/rollback evidence;
- privacy-bounded telemetry fail-open pilot.

Chưa có ở public product path:

- owner-scoped CAD Program lifecycle;
- public preview/commit/validate;
- trusted approval;
- full recovery/drop matrix và public rollback;
- CAD Program v1 modify/delete/pattern;
- skill/workflow platform;
- Scene Graph;
- full customer installer/update ecosystem;
- production scale/SLO/ecosystem.

## 2. Chuẩn hóa numbering

| Phase | Tên mới | Kết quả chính |
|---:|---|---|
| 5 | Runtime, Identity and Local Write Foundation | .NET R25 + LT compatibility + pairing/isolation + local write POC |
| 6 | Public CAD Program v0 and Managed Write Pilot | Prepare/preview/commit/validate create-only qua public Gateway |
| 7 | Durable Recovery, Trusted Approval and Rollback | C2 control envelope, drop recovery, consent và rollback |
| 8 | CAD Program v1 and Cross-Runtime Capability | Variables, pattern, modify/delete, annotation và capability tiers |
| 9 | Skill and Workflow Platform | Skill catalog, durable workflows, pause/resume/approval/recovery |
| 10 | Scene Graph and Drawing Intelligence | Relation graph, contour/feature inference, validation nâng cao |
| 11 | Packaging, Distribution and Multi-User Pilot | Installer, signed releases, update/rollback và customer pilot |
| 12 | Production Hardening, Scale and Ecosystem | SLO, quota, backup/restore, scale và skill/capability ecosystem |

Roadmap cũ đặt identity ở Phase 6 và kết thúc ở Phase 13. Mục tiêu identity đã được thực hiện trong Phase 5.6 nên các phase sau dịch lên một số. Tài liệu hoặc issue cũ vẫn có giá trị lịch sử nhưng numbering mới là nguồn hiện hành.

## 3. Nguyên tắc thứ tự

1. Không mở public write trước khi owner isolation và runtime evidence đủ mạnh.
2. Không mở broad CAD Program trước preview/commit/validation create-only.
3. Không cho model tự approve.
4. Không auto retry write đã started hoặc chưa chứng minh not-started.
5. Không mở modify/delete trước recovery và rollback.
6. Không xây skill/workflow trước khi CAD Program contract ổn định.
7. Không xây Scene Graph lớn trước snapshot/revision/entity model đủ tin cậy.
8. Mọi phase thay runtime contract phải chạy LT compatibility regression.
9. Không tuyên bố support release chỉ vì compile; cần real load/smoke.
10. AutoCAD Full không bị hạ xuống giới hạn của AutoCAD LT.

---

# Phase 6 — Public CAD Program v0 and Managed Write Pilot

## Mục tiêu

Productize local create-only POC thành public owner-scoped flow:

```text
Observe → Prepare → Preview → Commit → Validate
```

## Phạm vi

- `cad.program/0.2` runtime-neutral;
- create-only primitives;
- public FastMCP write tools/resources;
- owner-scoped program/preview/validation/receipt storage;
- typed Gateway–Agent commands;
- Agent write executor qua RuntimeBroker;
- Managed .NET R25 primary write path;
- runtime/package/capability/registry/policy pinning;
- kill switches và allowlisted pilot;
- live Mechanical 2025 E2E.

## Exit

- preview không đổi DWG;
- commit tạo effect một lần;
- exact duplicate không duplicate;
- stale document/runtime/package/policy bị chặn;
- cross-owner access `not_found`;
- hard pause/write lock chặn trước Host;
- LT vẫn read-only và regression xanh.

## Chưa làm

Trusted approval, full drop recovery, public rollback, broad modify/delete, LT write và customer rollout.

Chi tiết: [Phase-6.md](./Phase-6.md).

---

# Phase 7 — Durable Recovery, Trusted Approval and Rollback

## Mục tiêu

Biến Phase 6 create-only write path thành C2 control envelope đủ an toàn cho pilot giới hạn.

## Phạm vi

- immutable execution intent;
- one-time owner-scoped consent;
- Agent local confirmation và Portal recent-auth approval;
- atomic consume/release;
- ordered execution evidence across Gateway/Agent/Host;
- drop matrix trước/sau ACK, transaction, effect và result;
- no retry sau started;
- `outcome_unknown` recovery case;
- checkpoint, rollback preview và conflict detection;
- hard pause, revoke và kill switches xuyên state machine;
- operator diagnostics và manual recovery.

## Exit

- mọi tested drop point không duplicate effect;
- outcome được chứng minh hoặc chuyển needs-attention;
- approval bind exact user/device/document/program/preview/runtime/package/policy/TTL;
- một consent release tối đa một job;
- rollback chỉ chạy khi conflict-free;
- model không thể tự approve.

## Chưa làm

Broad CAD operations, team sharing, skill workflows, Scene Graph và production scale.

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
- block insert/attributes;
- annotation/dimension mở rộng;
- erase/delete có risk floor;
- program patch/rebase;
- reusable component refs;
- validation profiles.

## Capability tiers

- `portable_core`;
- `managed_standard`;
- `managed_advanced`;
- `lt_compat`;
- `headless_only`;
- `experimental`.

## Exit

- v1 program chạy trên Full/.NET;
- operation unsupported fail `capability_missing`;
- high-risk operations yêu cầu Phase 7 trusted approval;
- preview invalid khi registry/compiler/runtime thay đổi;
- portable subset có conformance evidence trước khi mở LT.

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
- workflow không được bypass Gateway policy;
- không arbitrary third-party code.

## Exit

- ít nhất ba workflows thật: auto-dimension, drawing cleanup và one domain workflow;
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
- no inference used as destructive authority without revalidation.

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
- previous-known-good rollback;
- diagnostics/support bundles;
- onboarding không yêu cầu `.env`, terminal, port hoặc tunnel;
- limited customer pilot.

## Exit

- clean install/upgrade/rollback trên clean VMs;
- exact family component selection;
- LT không load Managed Host;
- signed artifact validation fail closed;
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
- approval chỉ trusted channel;
- replay/idempotency tests;
- audit correlation end-to-end.

### Runtime

- Managed .NET primary cho Full;
- LT compatibility không regression;
- no silent fallback cho write;
- ezdxf non-authoritative cho live DWG safety;
- release support chỉ sau real evidence.

### Operations

- kill switches riêng `managed_write`, `lt_write`, `high_risk`, operation packs;
- telemetry fail open;
- no update giữa active job;
- previous-known-good rollback;
- external production signing gates rõ ràng.

## 5. Tài liệu ưu tiên

Khi mâu thuẫn:

1. [fastmcp-multi-user-autocad-plan.md](./fastmcp-multi-user-autocad-plan.md) quyết định architecture boundaries.
2. Tài liệu phase cụ thể quyết định scope/contract/exit gate của phase đó.
3. Tài liệu này quyết định numbering và thứ tự Phase 6–12.
4. [appendix-user-interface.md](./appendix-user-interface.md) quyết định cách render state và trusted actions.
5. Evidence documents quyết định điều đã thực sự chứng minh; roadmap không tự biến kế hoạch thành support claim.
