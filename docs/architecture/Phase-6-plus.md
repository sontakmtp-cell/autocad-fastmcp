# Roadmap triển khai Phase 6–13 — AutoCAD MCP đa runtime

> Trạng thái cập nhật 2026-07-24: Phase 4 C1 đã GO. Phase 5 đang được lập kế hoạch theo hướng Managed .NET primary cho AutoCAD Full, AutoLISP/File IPC compatibility cho AutoCAD LT và ezdxf cho headless/offline/test. Tài liệu này bổ sung các phase sản phẩm độc lập sau Phase 5.
>
> Baseline: nhánh `phase-5`, tiếp nối `docs/architecture/fastmcp-multi-user-autocad-plan.md`.
>
> Đây là tài liệu kế hoạch. Không triển khai code trong tài liệu này.

## 1. Vì sao phải tách Phase 6+

Phase 5 chỉ nên giải quyết nền runtime:

- runtime broker trong Desktop Agent;
- Managed .NET Host trong AutoCAD Full;
- capability/runtime evidence;
- entity observation và revision mạnh hơn;
- transaction spike;
- build-family feasibility;
- bảo toàn AutoCAD LT compatibility.

Không nên nhét identity, broad write, workflow, Scene Graph, installer và production scale vào các tiểu mục `5.x`. Làm vậy khiến một phase không có điểm kết thúc rõ và khó rollback.

Roadmap mới:

| Phase | Tên | Kết quả chính |
|---:|---|---|
| 5 | Managed .NET Runtime Foundation | AutoCAD Full dùng .NET primary; LT compatibility không regression |
| 6 | Production Identity, Pairing and Tenant Isolation | Nhiều user/device được pair, revoke và cô lập đúng |
| 7 | CAD Program v0 and Trusted Write Path | Create-only program, preview, commit và validation an toàn |
| 8 | Durable Execution Recovery, Approval and C2 | Mất mạng/crash không duplicate; approval và rollback hoàn chỉnh |
| 9 | CAD Program v1 and Cross-Runtime Capability | Modify/delete/pattern/annotation theo capability runtime |
| 10 | Skill and Workflow Platform | Auto-dimension, P&ID và workflow bền vững |
| 11 | Scene Graph and Drawing Intelligence | Quan hệ hình học, feature inference và validation nâng cao |
| 12 | Packaging, Distribution and Multi-User Pilot | Installer, signed bundle, update/rollback và customer pilot |
| 13 | Production Hardening, Scale and Ecosystem | SLO, quota, backup/restore, scale và phát hành skill/capability |

## 2. Nguyên tắc thứ tự

1. Không mở production multi-user trước khi runtime/device manifest ổn định.
2. Không mở write nhiều user trước khi tenant isolation đạt.
3. Không mở broad CAD Program trước khi preview, transaction và failure semantics chạy trên AutoCAD thật.
4. Không xây skill/workflow trước khi CAD Program contract ổn định.
5. Không xây Scene Graph lớn trước khi snapshot/revision/entity model đủ tin cậy.
6. Mọi phase thay runtime contract phải chạy lại AutoCAD LT compatibility gate.
7. Không tuyên bố hỗ trợ AutoCAD release chỉ vì compile được; phải load/smoke trên AutoCAD thật.

---

# Phase 6 — Production Identity, Pairing and Tenant Isolation

## 6.1. Mục tiêu

Chuyển lab credential và owner cố định của Phase 4 thành identity/device lifecycle có thể dùng cho nhiều khách hàng, trên device model đã nhận biết runtime và capability.

## 6.2. Phạm vi bắt buộc

- Map `(issuer, sub)` từ Auth0 sang internal user.
- Không dùng email, `client_id` hoặc `azp` làm device owner.
- Browser pairing flow với one-time code, TTL, consumed state và audit.
- Device key hoặc credential production-grade, bảo vệ bằng DPAPI/Credential Manager.
- Challenge-response khi mở WSS session.
- Device ownership, rename, default device, revoke và credential rotation.
- Agent session replacement: session mới thay session cũ có kiểm soát.
- Owner-filtered repository cho device, session, job, snapshot, artifact, program, consent và audit.
- Managed Host không nhận ChatGPT token và không tự xác thực user; nó chỉ tin authenticated local Agent session.
- Hai user, hai device, trong đó có thể một máy Full/.NET và một máy LT compatibility.

## 6.3. Luồng sau Phase 6

```text
User login Auth0
→ pair Desktop Agent
→ Gateway bind user/device
→ Agent discover AutoCAD edition/runtime
→ publish owner-scoped capability manifest
→ ChatGPT chỉ thấy device thuộc user đó
```

## 6.4. Exit criteria

- User A không thể list, observe, query, cancel, job-read hoặc resource-read device B.
- Đổi ID trực tiếp, pairing replay, token sai `sub`, device/session swap đều bị deny trước dispatch.
- Revoke đóng active WSS và chặn reconnect.
- Session cũ không thể gửi result sau khi bị session mới thay thế.
- Audit ghi đủ actor, owner, device, runtime, session, job và correlation ID.
- Identity semantics giống nhau giữa .NET primary và LT compatibility.

## 6.5. Kiểm thử

- Auth0 issuer, audience, expiry, scope và `sub`.
- Pairing expiry, replay, consume race và browser-session mismatch.
- IDOR fuzz trên toàn public tool/resource surface.
- Stolen old credential, key rotation và revoked device.
- Artifact/job/snapshot ownership isolation.
- User A Full/.NET và user B LT compatibility.

## 6.6. Chưa làm

- Broad customer write.
- Subscription billing hoàn chỉnh.
- Organization/team sharing ngoài owner model.

## 6.7. Rollback

- Disable new pairing.
- Revoke pilot devices.
- Phase 5 lab allowlist vẫn có thể chạy độc lập.
- Legacy/local profile không bị ảnh hưởng.

## 6.8. Demo

Hai tài khoản Auth0, hai máy và hai runtime khác nhau; mỗi user chỉ thấy và điều khiển đúng máy của mình.

---

# Phase 7 — CAD Program v0 and Trusted Write Path

## 7.1. Mục tiêu

Mở write thấp rủi ro bằng CAD Program runtime-neutral. AutoCAD Full dùng Managed .NET direct execution; AutoCAD LT chỉ chạy portable subset đã chứng minh.

## 7.2. CAD Program v0

- Step tuyến tính, không loop.
- Create-only primitives: line, circle, polyline, rectangle, layer ensure, text và dimension linear tối thiểu.
- Tham chiếu output bước trước.
- Preconditions: document, revision và capability.
- Postconditions: entity count, type và bounds.
- Budget: step, entity, time, payload và artifact.
- Program ID, revision, digest và patch invalidation.

## 7.3. Runtime execution

### Managed .NET

- Primitive map trực tiếp vào AutoCAD Database/Transaction API.
- Document lock và transaction rõ ràng.
- Preview ưu tiên transaction abort.
- Không generated AutoLISP trong primary path.

### AutoCAD LT compatibility

- Chỉ dùng packaged operation hoặc compiler sang allowlisted AutoLISP.
- Chỉ công bố `portable_core` đã qua conformance.
- Unsupported operation trả `capability_missing`.

### ezdxf

- Headless preview, golden validation và DXF offline.
- Không dùng headless result để authorize live DWG commit.

## 7.4. Public Gateway delta

Mở theo feature flag:

- `cad_prepare_program`
- `cad_preview`
- `cad_commit`
- `cad_validate`

Mọi preview/commit phải pin:

- program revision/digest;
- document revision;
- runtime ID/role;
- host/compiler/package version;
- operation registry hash;
- risk-policy version.

## 7.5. Exit criteria

- Create-only program preview, commit và validate trên Mechanical 2025 qua .NET.
- Runtime đổi sau preview làm commit bị từ chối.
- Program patch làm preview/consent cũ mất hiệu lực.
- Invalid capability, stale revision, budget vượt mức và path escape bị chặn tại Gateway và Agent/Host.
- Ít nhất một portable primitive có conformance evidence trên LT, hoặc được ghi rõ chưa hỗ trợ.

## 7.6. Kiểm thử

- Property/fuzz schema và references.
- Preview abort khôi phục document.
- Duplicate idempotency và payload mismatch.
- Host/compiler/package mismatch.
- Cross-runtime geometry tolerance.
- Agent/Gateway restart trước commit.

## 7.7. Rollback

- Kill switch riêng cho `managed_write` và `lt_write`.
- Disable four write tools; observe/query vẫn chạy.
- Compatibility runtime giữ read-only.

## 7.8. Demo

ChatGPT tạo một tấm chữ nhật có bốn lỗ bằng CAD Program v0, preview, commit và validate trên Full/.NET. LT chỉ chạy khi manifest có đủ portable primitives.

---

# Phase 8 — Durable Execution Recovery, Approval and C2

## 8.1. Mục tiêu

Hoàn thiện semantics mất mạng/crash trong lúc write, trusted approval, rollback và operator recovery trên AutoCAD thật.

## 8.2. Phạm vi

- Local execution ledger bền qua Agent restart.
- Managed Host evidence/ledger đủ để reconcile.
- Drop matrix: trước ACK, sau ACK, trước transaction, trong transaction, sau effect, trước result và sau result.
- Giữ invariant `reconnect_pending`, `outcome_unknown`, `needs_attention` của Phase 3.1.
- Không tự retry write đã `started`.
- Trusted confirmation/approval qua Agent tray hoặc companion web đã login.
- Approval bind exact preview/execution digest, user, device, document, runtime, package/registry và TTL.
- Checkpoint/rollback có revision-conflict handling.
- AutoCAD close/crash, Host unload/reload và Agent/Gateway restart.
- Operator diagnostics cho unknown outcome và manual recovery.

## 8.3. Runtime-specific recovery

- .NET transaction chưa commit: chứng minh no effect.
- .NET commit xong nhưng mất result: reconcile bằng ledger, revision/event evidence và entity marker khi phù hợp.
- LT/File IPC: conservative, không gửi ESC hoặc retry write mù.
- Không fallback runtime cho job đang chạy.

## 8.4. Exit criteria

- Mọi drop point không tạo duplicate effect.
- Outcome được chứng minh hoặc chuyển `needs_attention`; không báo success giả.
- Approval không reuse sau program/runtime/document change.
- Rollback chạy khi revision hợp lệ; conflict trả report thay vì generic Undo.
- Hard pause và revoke hoạt động đúng state machine.

## 8.5. Rollback

- Disable commit/rollback public tools.
- Giữ prepare/preview cho lab nếu an toàn.
- Quay Agent về read-only compatibility.

## 8.6. Demo

Cắt WSS ở nhiều điểm của một write operation; job reconcile chính xác, drawing chỉ thay đổi tối đa một lần và có thể rollback bằng checkpoint.

---

# Phase 9 — CAD Program v1 and Cross-Runtime Capability

## 9.1. Mục tiêu

Mở capability CAD tổng quát hơn mà không kéo AutoCAD Full xuống giới hạn của LT hoặc AutoCAD phiên bản cũ.

## 9.2. Năng lực v1

- Variables và safe expression grammar.
- Bounded repeat/pattern.
- Selection/query refs trên immutable snapshot.
- Move, copy, rotate, scale, mirror, offset, fillet và chamfer.
- Delete/erase có scope và high-risk policy.
- Block insert và attributes.
- Annotation/dimension mở rộng.
- Program patch/rebase.
- Reusable component refs.
- Validation profile theo ngành.

## 9.3. Capability tiers

- `portable_core`: dự kiến chạy trên .NET và LT.
- `managed_standard`: chỉ Full/.NET.
- `managed_advanced`: vertical/release-specific.
- `lt_compat`: dispatcher-specific compatibility operation.
- `headless_only`: DXF/offline/test.

## 9.4. Quy tắc

- Không thêm MCP tool theo primitive.
- Prepare kiểm capability snapshot; Agent/Host kiểm lại trước execution.
- Unsupported op không được tự đổi thành operation gần giống.
- Delete, purge và broad mutation có risk floor cao.
- Registry/compiler upgrade phải invalid preview khi semantic có thể đổi.

## 9.5. Exit criteria

- Program dùng variables, pattern và modify chạy trên Full/.NET.
- Portable conformance suite chạy trên phạm vi .NET, LT và ezdxf đã công bố.
- Capability-specific program bị từ chối sớm trên runtime không hỗ trợ.
- Delete/high-risk luôn preview và one-time approval.

## 9.6. Rollback

- Disable `cad.program/1`; v0 vẫn chạy.
- Disable individual operation pack qua manifest/kill switch.

## 9.7. Demo

Vẽ mặt bích bằng repeat/pattern, sửa bằng program patch và chạy trên Full; LT nhận portable variant hoặc báo capability thiếu rõ ràng.

---

# Phase 10 — Skill and Workflow Platform

## 10.1. Mục tiêu

Đưa tính năng chuyên ngành vào skill/workflow nhưng vẫn giữ đường CAD Program tự do cho yêu cầu mới.

## 10.2. Skill model

Một skill versioned gồm:

- intent/knowledge/examples;
- typed inputs;
- required capability tiers;
- observation/query strategy;
- CAD Program template hoặc generator;
- validation profile;
- risk/consent floor;
- recovery/rollback guidance;
- supported runtime/release/product range.

## 10.3. Workflow engine

- Durable workflow state trong Gateway DB.
- Pause, resume, retry, patch và re-preview.
- `waiting_for_agent`, `waiting_for_user`, `waiting_for_approval`, `needs_patch`.
- Cho phép chèn CAD Program do ChatGPT tạo giữa các bước skill.
- Không có skill phù hợp thì đi thẳng observe → prepare program.
- FastMCP task không làm source of truth.

## 10.4. Skill đầu tiên

- Auto-dimension: detect → plan → preview → confirm → commit → audit/repair.
- P&ID: chỉ hiện khi device có library/license/dependency.
- Batch cleanup và layer standardization là workflow, không phải public tool mới.

## 10.5. Distribution

- Skill catalog ở Gateway/resource.
- Primitive thực thi nằm trong signed Agent/Host/AutoLISP package.
- Skill không gửi arbitrary DLL/C#/LISP tới máy.
- Skill version pin schema, validation profile và capability requirements.

## 10.6. Exit criteria

- Auto-dimension chạy qua generic job/program/risk/approval.
- Workflow sống qua Gateway restart.
- Custom request không dùng skill vẫn chạy bằng CAD Program.
- Skill không tương thích runtime bị ẩn hoặc fail trước dispatch.

## 10.7. Rollback

- Disable skill/version theo tenant/device.
- Direct CAD Program path vẫn chạy.

## 10.8. Demo

ChatGPT dùng auto-dimension skill, sau đó tạo một CAD Program tự do để sửa chi tiết ngoài phạm vi skill.

---

# Phase 11 — Scene Graph and Drawing Intelligence

## 11.1. Mục tiêu

Giúp ChatGPT hiểu drawing bằng entity, quan hệ và feature thay vì danh sách object thô.

## 11.2. Lộ trình

1. Normalized entity snapshot.
2. Spatial index và relation graph.
3. Contour/region và annotation links.
4. Feature inference: hole, slot, centerline, repeated pattern và part.
5. Anomaly validation: overlap, detached dimension, duplicate geometry và invalid relation.

## 11.3. Phân bổ tính toán

- Managed Host: extraction cần AutoCAD object/database semantics.
- Agent: bounded normalization và artifact capture.
- Gateway worker: immutable snapshot index/query.
- ezdxf: golden fixtures và DXF-only inference với headless evidence.

## 11.4. Public data flow

- `cad_observe` trả bounded summary.
- `cad_query` dùng cursor/filter.
- Resource/artifact chứa entity/relation/feature pages.
- Inference có confidence/evidence.
- Scene cũ không authorize commit khi document revision đổi.

## 11.5. Exit criteria

- Query inside, intersect, parallel, perpendicular, concentric, aligned và connected.
- Hole/slot inference có accuracy target và evidence.
- Context size, pagination và latency nằm trong budget.
- Vertical/custom object không bị flatten sai mà không cảnh báo.

## 11.6. Rollback

- Tắt relation/feature layer.
- Quay về entity snapshot/query cơ bản.

## 11.7. Demo

ChatGPT tìm bốn lỗ trong contour lớn nhất, nhận diện pattern và tạo program chỉnh khoảng cách mà không tải toàn bộ graph vào context.

---

# Phase 12 — Packaging, Distribution and Multi-User Pilot

## 12.1. Mục tiêu

Biến POC thành gói cài đặt có thể bàn giao cho customer pilot và hỗ trợ được.

## 12.2. Packaging

- Signed Desktop Agent installer.
- Managed Host bundle nhiều release family.
- AutoLISP package cho LT.
- Manifest, hash, SBOM và malware scan.
- Clean Windows VM install/uninstall/upgrade/rollback.
- SECURELOAD/trusted-path diagnostics.

## 12.3. Update lifecycle

- Gateway công bố min/recommended Agent/Host/package version.
- Staged rollout theo cohort.
- Signed update manifest.
- Không chạy arbitrary installer command từ Gateway.
- Không update Host giữa active job.
- Giữ previous known-good package để rollback.

## 12.4. User experience

- Pairing/login dễ dùng.
- Tray hiển thị Gateway, AutoCAD, runtime, document và hard pause.
- Diagnostics bundle lọc secret/path nhạy cảm.
- Hiển thị rõ primary, compatibility, degraded và fallback state.
- Companion web tối thiểu cho device, revoke, policy và approval.

## 12.5. Pilot

- Cohort AutoCAD Full dùng .NET primary.
- Cohort LT dùng compatibility.
- Kill switch tách theo runtime và risk.
- Support matrix theo release/vertical.
- Telemetry latency, error, unknown outcome và update theo runtime family.

## 12.6. Exit criteria

- Clean-machine install và rollback đạt.
- Device revoke, credential rotation và package rollback được diễn tập.
- Pilot không cross-tenant.
- Support xác định được lỗi tại Gateway, Agent, Managed Host, LT dispatcher hoặc drawing.
- LT không bị ép cài Managed Host.

## 12.7. Demo

Một user Full và một user LT cài từ installer, pair tài khoản, chạy đúng runtime và nhận update/rollback có kiểm soát.

---

# Phase 13 — Production Hardening, Scale and Ecosystem

## 13.1. Mục tiêu

Đưa hệ thống từ pilot thành dịch vụ vận hành bền và chuẩn bị cách phát hành capability/skill mà không làm nổ public MCP tool set.

## 13.2. Operations

- SLO cho Gateway, device presence, job latency và unknown outcome.
- Backup/restore drill.
- Disk-full, DB lock, proxy/JWKS outage và certificate renewal.
- Audit retention, privacy và restricted artifact access.
- Quota theo user/device/job/artifact/risk.
- Incident runbook và support escalation.

## 13.3. Scale gates

Chỉ chuyển khi metric chứng minh cần:

- SQLite → Postgres cho multi-writer/HA.
- In-memory connection registry → Redis/NATS/broker cho multi-process routing.
- Local artifact disk → S3-compatible storage.
- Scene/validation CPU → bounded worker pool.

Không migrate chỉ vì production thường dùng stack lớn.

## 13.4. Capability và skill ecosystem

- Signed/versioned operation packs trong Agent/Host/AutoLISP package.
- Gateway skill catalog versioned và cohort allowlist.
- Compatibility manifest giữa Gateway, Agent, Host, runtime và skill.
- Không biến mỗi skill thành MCP tool.
- Không cho third-party skill mang arbitrary DLL/C#/LISP vào default trust domain.
- Có review/signing/security process nếu mở package bên thứ ba.

## 13.5. Commercial/admin readiness

- Quota/subscription hooks tách khỏi Auth0 scope.
- Admin tenant-aware, action có reason và audit.
- Organization/team sharing chỉ mở sau owner model và explicit grants ổn định.

## 13.6. Exit criteria

- Restore, update rollback, revoke và incident drill đạt định kỳ.
- Load/soak test đạt production target.
- Scale architecture chỉ kích hoạt theo threshold đo được.
- Phát hành capability/skill không đổi public MCP contract.
- Security review độc lập cho Gateway, Agent–Host IPC, installer/update và tenant isolation.

## 13.7. Rollback

- Stop new admissions.
- Disable writes/high-risk/advanced packs.
- Drain jobs và pin previous Gateway/Agent/Host/package.
- Restore DB/artifact metadata theo runbook.

## 13.8. Demo

Phát hành một primitive/skill mới cho cohort nhỏ, không thêm MCP tool, có version negotiation, staged rollout, telemetry và rollback.

---

# 14. POC order và gate mở khóa

| Thứ tự | Phase | POC/Gate | Bằng chứng | Mở khóa |
|---:|---:|---|---|---|
| 1 | 5 | Runtime seam | C1 chạy qua broker không regression | Managed .NET pivot |
| 2 | 5 | .NET read-only | Public E2E qua Named Pipe/Host | Entity/revision |
| 3 | 5 | Entity/revision | Query và stale tests | Transaction spike |
| 4 | 5 | .NET transaction | Preview abort, commit, digest | Trusted write design |
| 5 | 5 | Release families | Real load/smoke trên old + current family | Support floor |
| 6 | 5 | LT gate | LT regression/real smoke | Runtime pivot GO |
| 7 | 6 | Two-user/two-device | Pairing, ownership, revoke, IDOR deny | Multi-user write |
| 8 | 7 | CAD Program v0 | Double validation và runtime pinning | C2 failure tests |
| 9 | 8 | Disconnect/C2 | Drop matrix, approval và rollback | Customer write pilot |
| 10 | 9 | CAD Program v1 | Modify/delete/pattern/capability | Skill platform |
| 11 | 10 | Workflow | Auto-dimension sống qua restart | Scene intelligence |
| 12 | 11 | Scene Graph | Accuracy, evidence và bounded context | Installer pilot |
| 13 | 12 | Distribution pilot | Clean install/update/rollback/support | Production hardening |
| 14 | 13 | Production gate | SLO, restore, load và security review | Broad production cohort |

# 15. Definition of Done toàn roadmap

Sản phẩm chỉ được coi là hoàn thành roadmap Phase 5–13 khi:

- AutoCAD Full 2018+ dùng Managed .NET primary theo release family đã chứng minh.
- AutoCAD LT 2024+ vẫn dùng compatibility runtime và không mất chức năng hiện tại.
- Public MCP tools không tách theo runtime hoặc skill.
- User/device ownership và revoke fail closed.
- CAD Program v0/v1 được validate ở Gateway và Agent/Host.
- Preview/commit pin runtime, revision và execution digest.
- Mất mạng/crash không tạo duplicate write.
- Approval đến từ trusted human channel, không phải boolean do model tự gửi.
- Skill là knowledge/workflow trên CAD Program, không phải arbitrary code package.
- Scene data bounded, versioned và gắn evidence.
- Installer/update/rollback có chữ ký, hash và support diagnostics.
- Production scale chỉ thay đổi theo metric, không theo phỏng đoán.
- Capability/skill mới không bắt buộc thêm public MCP tool.