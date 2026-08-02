# Phase 11 — Packaging, Distribution and Multi-User Pilot

> Trạng thái: kế hoạch kiến trúc và triển khai sau khi Phase 10 được merge vào
> `main` qua PR #14.
>
> Baseline bắt buộc: commit
> `3f453622f420633734f603ced1092eec697ac2c2` hoặc commit mới hơn trên `main`
> có chứa toàn bộ PR #14.
>
> Phase 10 đạt **Engineering GO** cho Scene Graph and Drawing Intelligence với
> 72/72 hosted checks, retained live AutoCAD Mechanical 2025 R25 evidence,
> Gateway restart, Agent reconnect và no-CAD-effect proof. Phase 10 vẫn giữ
> **Customer Pilot NO-GO** vì production signing, trusted timestamp,
> distribution, release-family certification, support operations và pilot
> cohort thuộc Phase 11.
>
> Phase 11 biến các engineering artifacts của Phase 5–10 thành một release train
> có thể cài đặt, xác minh, cập nhật, rollback, chẩn đoán và vận hành cho một
> nhóm người dùng thật có giới hạn. Phase này không phải Phase 12 scale-out,
> không mở arbitrary plugin marketplace và không làm yếu bất kỳ authority,
> owner-isolation hoặc no-duplicate-write invariant nào đã đạt.

---

## 0. Chỉ dẫn bắt buộc cho Codex local

Codex thực hiện Phase 11 trong local repository. Bắt đầu từ `main` mới nhất
nhưng **không làm trực tiếp trên `main`**.

### 0.1. Tạo nhánh triển khai

```powershell
git status --short
git fetch origin
git checkout main
git pull --ff-only origin main
git checkout -b codex/phase-11-packaging-distribution-pilot
```

Nếu working tree không sạch, `main` không fast-forward được, hoặc nhánh trên đã
có lịch sử không rõ nguồn gốc:

- không force;
- không reset;
- không xóa thay đổi local;
- dừng mutation;
- ghi rõ blocker;
- chỉ tiếp tục review read-only nếu còn hữu ích.

Nhánh triển khai phải sinh từ `main` chứa PR #14. Không dùng lại nhánh Phase 10
làm baseline.

### 0.2. Tài liệu và code phải đọc trước

Đọc tối thiểu:

```text
docs/architecture/fastmcp-multi-user-autocad-plan.md
docs/architecture/Phase-6-plus.md
docs/architecture/Phase-7.md
docs/architecture/Phase-8.md
docs/architecture/Phase-9.md
docs/architecture/Phase-10.md
docs/architecture/appendix-user-interface.md
docs/architecture/phase5-signing-clean-vm-runbook.md
docs/architecture/phase5-signing-clean-vm-evidence.md
docs/architecture/phase5-local-telemetry-pilot-evidence.md
docs/architecture/phase56-identity-isolation-evidence.md

docs/security/phase7-threat-model.md
docs/security/phase8-threat-model.md
docs/security/phase9-threat-model.md
docs/security/phase10-threat-model.md

scripts/build-phase5-managed-host.ps1
scripts/new-phase5-signed-r25-release.ps1
scripts/test-phase5-install-rollback.ps1
scripts/test-phase5-clean-vm-rollback.ps1
tests/test_phase5_packaging_security.py

apps/desktop_agent/
apps/web_portal/
services/gateway/
packages/contracts/
native/autocad_managed_host/
```

Tên file có thể đã thay đổi sau baseline. Codex phải tìm implementation hiện
hành thay vì tạo song song một pipeline mới chỉ vì tên cũ chứa `phase5`.

Mọi nhận xét về baseline phải dẫn tới file, class, function, schema, migration,
workflow hoặc test cụ thể. Phân biệt rõ:

- đã xác minh;
- suy luận;
- chưa đủ dữ liệu;
- quyết định mới của Phase 11.

### 0.3. Baseline trước khi sửa code

Chạy ít nhất:

```powershell
python scripts/test-phase10-conformance.py
python scripts/validate-phase10-live-evidence.py --root .
```

Sau đó chạy các suite hiện hành của:

- root Python;
- Gateway;
- contracts;
- Desktop Agent;
- Managed .NET Host Core và Host contracts;
- Portal unit và Portal E2E;
- Phase 7 recovery/approval/rollback;
- Phase 8 CAD Program v1;
- Phase 9 workflow;
- Phase 10 scene intelligence;
- Phase 5 packaging/signing safety.

Baseline gần nhất của PR #14 gồm:

- Phase 10 hosted checks: 72/72 passed;
- root local suite: 582 passed, 1 skipped;
- retained R25 evidence validator: passed.

Không hard-code các count này thành acceptance vĩnh viễn. Ghi lại command thật,
commit, OS, Python/.NET/Node/PowerShell versions, test counts và skips của
baseline hiện tại.

Không bắt đầu implementation nếu:

- Phase 10 conformance hoặc retained evidence đỏ;
- Phase 6–10 write/recovery/workflow/scene regression đỏ;
- owner isolation hoặc OAuth lifecycle regression đỏ;
- package/security validator hiện hành đỏ;
- working tree có thay đổi không giải thích được.

### 0.4. Quy tắc làm việc

- Không rollback hoặc viết lại Phase 0–10.
- Không đổi public MCP semantics chỉ để thuận tiện cho installer/updater.
- Không cho installer/updater gọi CAD write path.
- Không update Agent/Host khi có effect-bearing job đang active hoặc outcome
  chưa được resolve.
- Không tải hoặc chạy arbitrary executable, PowerShell, DLL, LISP, URL hay path
  do model/user cung cấp.
- Không tin TLS một mình; artifact phải được verify bằng signature và digest.
- Không dùng self-signed lab certificate để claim Customer Pilot GO.
- Không quảng cáo release family/runtime chưa có real evidence.
- LT không được load Managed Host.
- Không silent fallback package/family/runtime.
- Không xóa install receipt, release evidence, recovery case hoặc audit để làm
  rollback “trông như thành công”.
- Không gửi raw drawing, token, device private key hoặc full user path trong
  diagnostic bundle.
- Mỗi slice phải có contract, test, failure behavior, rollback/disable path và
  retained evidence.
- Commit nhỏ theo slice; không gom toàn bộ Phase 11 thành một commit khổng lồ.

---

## 1. Executive summary

Sau Phase 10, product path đã có:

```text
ChatGPT Web
→ OAuth + FastMCP Gateway
→ owner-scoped durable programs/workflows/scenes/jobs
→ outbound WSS Desktop Agent
→ RuntimeBroker
   ├─ Managed .NET Host R25 primary
   ├─ AutoLISP/File IPC compatibility
   └─ ezdxf headless/offline
→ trusted approval, commit, validation, recovery and rollback
```

Nhưng việc đưa hệ thống cho người dùng thật vẫn còn phụ thuộc vào operator và
lab procedures:

- R25 release mới chỉ có lab signing/rollback engineering;
- production CA certificate, timestamp và key custody chưa được chốt;
- installer chưa phải một onboarding product hoàn chỉnh;
- package selection theo AutoCAD family chưa được chứng minh xuyên suốt;
- Portal download/update UX chưa là durable release truth;
- update/rollback chưa có staged cohort policy hoàn chỉnh;
- diagnostics/support bundle chưa có contract production;
- telemetry soak và support ownership chưa hoàn tất;
- chưa có customer cohort evidence với nhiều owner/device;
- older Full family và real AutoCAD LT chưa được chứng nhận để support claim.

Phase 11 tạo release architecture:

```text
Source commit + locked build environment
→ reproducible component builds
→ SBOM + provenance + malware review
→ CA Authenticode signing + trusted timestamp
→ immutable release manifest
→ Portal release catalog
→ runtime-aware installer/probe
→ verified staged install/update
→ health gate
→ previous-known-good rollback
→ redacted diagnostics and support runbooks
→ limited multi-owner pilot
```

Kiến trúc chốt:

1. **Release manifest là immutable server-side truth.**
2. **Mỗi artifact bind exact commit, component version, family, digest, signer,
   timestamp và compatibility range.**
3. **Installer/updater verify fail-closed trước mọi mutation.**
4. **Một release train có thể chứa nhiều component nhưng không giả định mọi
   component luôn cùng version.**
5. **Compatibility manifest quyết định tổ hợp hợp lệ; filename hoặc latest tag
   không quyết định.**
6. **Runtime probe chọn đúng family; unsupported/ambiguous host dừng an toàn.**
7. **Không update giữa effect-bearing job, unresolved outcome hoặc pending
   rollback/recovery.**
8. **Rollback chỉ về exact previous-known-good đã verify và được policy cho phép.**
9. **Portal/Agent render release truth; UI không tự suy đoán support/readiness.**
10. **Multi-user pilot nghĩa là nhiều owner/device thật với isolation evidence,
    không phải nhiều local profiles dùng cùng một synthetic owner.**
11. **Phase 11 không mở production scale, billing, marketplace hoặc arbitrary
    third-party extension.**
12. **Customer Pilot GO là gate riêng, cao hơn repository Engineering GO.**

---

## 2. Baseline đã xác minh sau Phase 10

### 2.1. Phase 10 handoff

PR #14 đã merge vào `main` tại:

```text
3f453622f420633734f603ced1092eec697ac2c2
```

Retained Phase 10 evidence chứng minh:

- public scene tools/resources vẫn read-only;
- live R25 source projection và scene inference;
- unchanged document revision và DWG digest;
- Gateway restart và Agent reconnect;
- no duplicate scene/CAD effect;
- no-write DB digest không đổi;
- full hosted checks xanh trên final PR head.

Phase 11 không được yêu cầu recapture Phase 10 evidence cho mọi packaging PR,
nhưng release candidate cuối phải chạy lại Phase 6–10 conformance trên exact
signed package set được đưa vào pilot.

### 2.2. Packaging/signing foundation hiện có

Phase 5 đã chứng minh ở lab:

- build R25 Managed Host bundle;
- Authenticode lab signing;
- release manifest và post-sign hashes;
- clean install;
- upgrade với previous-known-good backup;
- exact rollback;
- tamper rejection;
- clean-VM rehearsal;
- production mode fail-closed nếu thiếu trusted timestamp hoặc valid chain.

Foundation này phải được productize, không tạo pipeline thứ hai bỏ qua các
validator hiện có.

### 2.3. External gates còn mở

Các input không thể giả lập bằng unit test:

- CA-issued production code-signing certificate;
- reviewed private-key custody, owner, rotation và revocation procedure;
- trusted Authenticode timestamp endpoint;
- approved SBOM/provenance/malware review workflow;
- public OAuth lifecycle trên pilot tenant;
- live revoke/re-pair drill;
- modal/busy, disconnect/reconnect và unknown-outcome drills;
- telemetry soak 3–7 ngày;
- support owner/on-call/incident rollback responsibility;
- explicit pilot cohort approval;
- at least one older Full family real load/smoke before support claim;
- real AutoCAD LT 2024+ certification before LT support claim.

### 2.4. Existing authority boundaries phải giữ nguyên

Phase 11 không thay đổi:

```text
observe/query/scene
→ prepare
→ preview
→ immutable execution intent
→ trusted approval when required
→ commit
→ validate
→ recovery/rollback
```

Possession of a signed installer, a newer package hoặc `autocad.write` scope
không bypass:

- owner/device/document binding;
- runtime/capability/registry/policy pinning;
- trusted approval;
- no-blind-retry;
- rollback conflict checks;
- Scene inference read-only authority boundary.

---

## 3. Mục tiêu Phase 11

1. Tạo strict immutable release contracts và compatibility manifest.
2. Tạo production signing pipeline dùng CA trust và trusted timestamp.
3. Sinh SBOM, build provenance, artifact hashes và malware-review evidence.
4. Productize Agent, Managed Host family bundles và LT compatibility package.
5. Tạo runtime-aware probe/installer chọn exact component family.
6. Tạo release catalog owner-safe cho Portal và Agent update shell.
7. Tạo staged update state machine với safe-window enforcement.
8. Tạo previous-known-good rollback và incident revoke/blocklist.
9. Tạo redacted diagnostics/support bundle có schema và size budget.
10. Hoàn thiện onboarding không cần `.env`, terminal, tunnel hoặc manual port.
11. Chứng nhận R25 và tối thiểu một older Full family bằng real load/smoke.
12. Chứng nhận real AutoCAD LT 2024+ cho compatibility scope thực sự hỗ trợ.
13. Chạy limited multi-owner, multi-device customer pilot.
14. Hoàn tất telemetry soak, support runbooks và incident drills.
15. Giữ Phase 6–10 conformance xanh trên exact release candidate.
16. Tạo evidence đủ để quyết định Customer Pilot GO/NO-GO và handoff Phase 12.

---

## 4. Ngoài phạm vi Phase 11

- PostgreSQL/queue/multi-worker migration chỉ vì có pilot users;
- production SLO toàn hệ thống và autoscaling;
- billing/subscription/quota enforcement;
- broad tenant admin/support console;
- public marketplace;
- arbitrary third-party DLL/LISP/Python/skill code;
- automatic machine-wide deployment qua enterprise fleet manager;
- macOS support;
- AutoCAD LT write nếu chưa có capability/evidence riêng;
- claiming all R22/R23/R24/R25 families supported chỉ vì build thành công;
- silent background update không có pilot policy;
- rollback database schema bằng destructive downgrade;
- cross-owner sharing/team workspaces;
- broad ecosystem governance, thuộc Phase 12.

---

## 5. Release units và compatibility model

### 5.1. Release units

Version độc lập tối thiểu:

- Gateway;
- Web Portal;
- Desktop Agent;
- Managed Host R22 bundle;
- Managed Host R23 bundle;
- Managed Host R24 bundle;
- Managed Host R25 bundle;
- AutoLISP/File IPC package;
- CAD Program schema/compiler/operation registry;
- Scene engine/profile;
- skill catalog/workflow definitions;
- Agent protocol;
- Host protocol;
- release manifest schema.

Phase 11 có thể phát hành một product release train gồm nhiều unit, nhưng phải
preserve version riêng và exact digest của từng unit.

### 5.2. Family matrix

| Family | Target AutoCAD | Managed runtime | Phase 11 rule |
|---|---:|---|---|
| R22 | 2018 | .NET Framework 4.6 family | build/probe allowed; support only after real evidence |
| R23 | 2019–2020 | .NET Framework 4.7 family | build/probe allowed; support only after real evidence |
| R24 | 2021–2024 | .NET Framework 4.8 family | preferred older-family certification target |
| R25 | 2025+ | .NET 8 Windows | required pilot primary family |
| LT | 2024+ | AutoLISP/File IPC | real certification; never load Managed Host |
| headless | no AutoCAD | ezdxf | offline/test only; not live DWG support |

Actual `SeriesMin`/`SeriesMax`, CLR/runtime requirements và vertical behavior
phải derive từ reviewed package metadata và real evidence, không từ bảng này
một cách máy móc.

### 5.3. Compatibility decision

Mỗi device resolve một `release_selection` chứa:

```text
product_release_id
agent_artifact_id
runtime_package_artifact_id
runtime_family
protocol_min/max
registry_version/hash
cad_program_schema_range
scene_engine/profile range
required_gateway_min
required_portal_min when applicable
support_status
selection_reason
```

Không chọn package bằng:

- filename substring;
- newest timestamp;
- model/user instruction;
- registry key duy nhất không cross-check;
- process name alone;
- “closest” family fallback.

Ambiguous hoặc unsupported selection trả typed state và không mutate máy.

---

## 6. Release contracts

### 6.1. Schema family

Tạo strict data-only contracts, dự kiến:

```text
cad.release-manifest/1
cad.release-artifact/1
cad.release-compatibility/1
cad.release-selection/1
cad.install-receipt/1
cad.update-state/1
cad.rollback-receipt/1
cad.support-bundle/1
cad.pilot-cohort/1
cad.pilot-evidence/1
```

Tên cuối có thể thay đổi trong ADR, nhưng boundary phải rõ.

### 6.2. Product release manifest

Manifest tối thiểu bind:

```text
release_id
channel
status: draft | candidate | pilot | revoked | superseded
source_commit
build_workflow_identity
build_environment_digest
created_at
component_artifacts[]
compatibility_matrix_digest
sbom_artifacts[]
provenance_artifact
malware_review_artifact
signing_policy_id
rollout_policy_id
minimum_versions
recommended_versions
known_issues
rollback_targets[]
```

Manifest được canonicalize và digest server-side. Không tin digest do browser,
Agent hoặc installer tự khai báo.

### 6.3. Artifact record

Mỗi artifact bind tối thiểu:

```text
artifact_id
component
version
platform/architecture
runtime_family
relative_path_or_distribution_key
size
sha256
content_type
signer_subject/thumbprint
signature_algorithm
signature_status
timestamp_status/timestamp_time
certificate_chain_evidence
sbom_ref
provenance_ref
compatibility_ref
revocation_status
```

Không đưa secret, signing key location hoặc private build credential vào public
manifest.

### 6.4. Canonical digest và immutability

- release candidate immutable sau khi ký;
- thay một byte, compatibility rule, support claim hoặc known issue tạo release
  ID/digest mới;
- không mutate artifact dưới cùng URL/key;
- revoked release vẫn retain record/evidence;
- Portal không cache một `latest` mutable blob như release truth;
- rollback target bind exact artifact digest.

### 6.5. Channel

Pilot tối thiểu có:

- `lab`;
- `internal`;
- `pilot`.

Không cần public stable/beta ecosystem trước Phase 12. Channel không bypass
signature hoặc compatibility checks.

---

## 7. Build, signing, SBOM và provenance

### 7.1. Build policy

Release build phải:

- bắt đầu từ clean checkout của exact commit;
- dùng locked dependencies/tool versions;
- không lấy executable dependency từ unreviewed mutable URL;
- tạo deterministic metadata khi feasible;
- record runner identity, OS, SDKs và command set;
- fail nếu working tree dirty hoặc submodule/dependency lock mismatch;
- output vào fresh staging root;
- không sign artifact trước khi test/scan pre-sign hoàn tất.

### 7.2. Production signing

Production mode yêu cầu:

- CA-issued certificate có Code Signing EKU;
- reviewed non-exportable/hardware-backed key custody khi khả thi;
- exact signer allowlist/policy;
- trusted timestamp;
- post-sign Authenticode status `Valid`;
- timestamp evidence;
- post-sign SHA-256;
- certificate expiry/rotation alert;
- revocation incident procedure.

Không:

- pass PFX password trên command line;
- export private key vào artifact/workspace;
- download certificate từ model-controlled URL;
- fall back sang self-signed khi production signing lỗi;
- publish unsigned partial release.

### 7.3. Signing coverage

Ký mọi executable-bearing artifact áp dụng:

- Desktop Agent executable/libraries;
- Managed Host assemblies;
- installer/bootstrapper;
- update/rollback executable/scripts nếu vẫn dùng script;
- LT dispatcher/package artifacts khi platform hỗ trợ verification policy;
- any native helper.

Manifest/hash verification vẫn bắt buộc cho file không hỗ trợ Authenticode.

### 7.4. SBOM và provenance

Tối thiểu:

- component-level SBOM;
- dependency names/versions/licenses/hashes;
- build source commit;
- build workflow/run identity;
- toolchain versions;
- artifact digests;
- signing identity/timestamp;
- provenance attestation;
- malware scan engine/version/result/time;
- reviewer/approval record.

Phase 11 không cần ecosystem-grade transparency log, nhưng evidence phải
machine-readable, retained và validator-enforced.

### 7.5. Fail-closed release publication

Release không thể chuyển sang `candidate` hoặc `pilot` nếu thiếu:

- all required artifacts;
- valid signatures/timestamps;
- manifest digest consistency;
- SBOM/provenance;
- malware review;
- compatibility matrix;
- rollback target hoặc explicit first-install policy;
- required conformance/evidence links;
- no open critical/high security blocker.

---

## 8. Installer và runtime-aware probe

### 8.1. Pilot installation scope

Mặc định Phase 11 dùng **per-user installation** cho Desktop Agent và AutoCAD
components để phù hợp current-user identity, Named Pipe ACL và limited pilot.
Machine-wide enterprise deployment deferred trừ khi ADR và evidence chứng minh
an toàn.

Installer phải giải thích rõ khi elevation thật sự cần; không tự nâng quyền âm
thầm.

### 8.2. Probe inputs

Probe bounded, read-only và allowlisted:

- Windows architecture/version;
- installed AutoCAD product/release/vertical;
- LT vs Full;
- running AutoCAD processes;
- approved Autodesk install/package roots;
- existing Agent/Host/LT package receipts;
- current component versions/hashes;
- disk space;
- required runtime availability;
- secure load/trusted path prerequisites ở mức safe diagnostic.

Không scan toàn disk, upload registry dump hoặc inspect arbitrary plugin content.

### 8.3. Selection rules

- Full R25 chọn R25 Managed Host;
- Full older release chọn exact certified family;
- LT 2024+ chỉ chọn LT compatibility package;
- LT không được cài/load Managed Host;
- unsupported family dừng trước mutation;
- multiple AutoCAD families có thể cài side-by-side bằng versioned package
  roots và exact package metadata;
- package change invalidates preview/approval đang tồn tại theo existing
  execution binding.

### 8.4. Installer state machine

```text
probe
→ resolve_release
→ download_or_open_staged_artifacts
→ verify_manifest
→ verify_signature_timestamp_hash
→ preflight
→ create_recovery_point/receipt
→ install_or_upgrade
→ local health verification
→ activate
→ persist receipt
```

Mọi state/error phải typed và restart-recoverable. Không dùng prose log làm
nguồn sự thật duy nhất.

### 8.5. Safe preflight

Block trước mutation nếu:

- AutoCAD đang chạy khi Host/LT package cần thay;
- Agent có effect-bearing job active;
- job ở `outcome_unknown`/`needs_attention`;
- rollback/recovery đang pending;
- hard pause/policy yêu cầu operator review;
- signature/timestamp/hash invalid;
- family selection ambiguous/unsupported;
- destination ngoài reviewed roots;
- installed bundle đã drift ngoài receipt;
- disk space không đủ;
- release revoked;
- protocol compatibility không đạt.

### 8.6. Onboarding

User-facing onboarding phải hoàn tất mà không yêu cầu:

- `.env` editing;
- terminal command;
- manual tunnel/port;
- copy token/private key;
- manual package folder copy;
- arbitrary trusted path change không giải thích.

Flow:

```text
Download signed installer
→ verify/present publisher and release
→ probe/select components
→ install
→ launch Agent
→ browser pairing
→ runtime health
→ read-only observation test
→ optional write enablement according pilot policy
```

---

## 9. Update và rollback architecture

### 9.1. Update state machine

```text
idle
→ update_available
→ downloading
→ downloaded
→ verified
→ staged
→ waiting_safe_window
→ installing
→ health_check
→ activated
→ completed
```

Failure states:

```text
verification_failed
preflight_blocked
install_failed
health_failed
rollback_required
rollback_in_progress
rollback_succeeded
rollback_failed
needs_support
```

State persist qua Agent restart. Gateway/Portal nhận owner-safe summary, không
nhận local secrets/raw paths.

### 9.2. Safe update window

Không install/activate khi:

- Agent đang dispatch/executing/validating effect-bearing job;
- unresolved outcome hoặc recovery case;
- pending trusted approval bind package hiện tại;
- rollback commit đang active;
- AutoCAD đang load package cần thay;
- local hard pause policy yêu cầu manual release;
- device session replacement chưa settle.

Pilot mặc định manual/explicit update. Automatic staged rollout chỉ được bật
sau soak evidence và vẫn phải chờ safe window.

### 9.3. Compatibility during rollout

Phải test:

- old Agent ↔ new Gateway;
- new Agent ↔ old-compatible Gateway;
- old Host ↔ new Agent trong negotiated range;
- new Host ↔ old-compatible Agent;
- package mismatch returns typed incompatible state;
- no silent protocol downgrade;
- active preview/consent invalidates khi relevant package/registry changes;
- read-only degraded mode chỉ khi policy explicit và không giả full readiness.

### 9.4. Previous-known-good rollback

Rollback chỉ dùng:

- exact verified install receipt;
- exact previous artifact digests;
- signed, non-revoked rollback target;
- policy-approved downgrade path;
- safe window;
- post-rollback health check.

Nếu installed files drift từ receipt:

- không overwrite mù;
- preserve displaced evidence;
- return `rollback_conflict` hoặc `needs_support`;
- produce redacted diagnostic references.

### 9.5. Anti-rollback và emergency rollback

- reject arbitrary old version;
- reject revoked/vulnerable release;
- allow exact emergency rollback target được server policy ký/approve;
- maintain minimum safe version;
- record who/what initiated rollback;
- Portal/model không được gửi arbitrary local path/version;
- kill switch có thể stop new activation mà không phá installed audit.

### 9.6. Gateway/Portal deployments

Phase 11 không xây full zero-downtime scale platform, nhưng release candidate
phải preserve:

- additive/backward-compatible migrations;
- durable job/workflow/scene truth;
- no destructive schema downgrade;
- rolling compatibility window với Agent;
- safe feature flag rollback;
- exact release correlation trong audit.

---

## 10. Portal và Desktop Agent UX

### 10.1. Portal release catalog

Portal hiển thị từ durable release truth:

- release/channel/status;
- supported/certified AutoCAD families;
- LT compatibility status;
- Agent/Host/LT versions;
- release date/size/hash;
- publisher/signature/timestamp summary;
- known issues;
- min/recommended versions;
- rollout cohort;
- revoked/superseded warning;
- upgrade/rollback guide;
- support contact/runbook link.

Không present uncertified family là supported.

### 10.2. Device update state

Portal/Agent hiển thị:

- installed component versions;
- selected runtime family;
- release compatibility;
- current/recommended/minimum release;
- update state và blocker;
- last successful install/rollback receipt;
- AutoCAD restart required;
- support/correlation ID.

Không dùng màu-only status.

### 10.3. Trusted actions

Các action mutation:

- start update;
- approve staged activation if policy requires;
- rollback;
- revoke device;
- collect diagnostic bundle;
- enable pilot write profile.

Phải owner-scoped, CSRF-protected, audited và recent-auth khi assurance yêu cầu.
Model không tự approve update/rollback hoặc pilot enablement.

### 10.4. Download delivery

- HTTPS;
- immutable artifact key;
- short-lived/authorized download reference khi cần;
- client verifies manifest/signature/hash sau download;
- no user-controlled URL redirect;
- no artifact path disclosure beyond safe metadata;
- interrupted download resume chỉ khi digest-bound.

---

## 11. Diagnostics và support bundle

### 11.1. Contract

Support bundle là explicit user/operator action, không auto-upload mặc định.

Manifest tối thiểu:

```text
support_bundle_id
schema_version
created_at
agent_version
runtime/package versions and hashes
safe product/release/vertical
health states
recent typed error codes
job/workflow/scene/program correlation IDs
install/update/rollback receipt summaries
telemetry exporter counters
redaction policy/version
included_sections
omitted_sections/reasons
bundle_digest
size
```

### 11.2. Redaction

Không mặc định chứa:

- OAuth token/session cookie;
- device private key/pipe secret;
- telemetry ingest token;
- full drawing path;
- raw DWG/DXF;
- raw entity/scene/program payload;
- user email/full owner subject;
- arbitrary registry dump;
- environment variables;
- browser storage;
- crash dump có thể chứa drawing memory.

Cho phép explicit opt-in attachment riêng chỉ khi policy/support flow được
review, không trộn vào default bundle.

### 11.3. Bounded behavior

- allowlisted files/fields;
- max bundle bytes;
- max event count/time window;
- deterministic redaction;
- no symlink/path traversal;
- no arbitrary glob;
- no upload endpoint do user/model cung cấp;
- local preview of included sections;
- exact digest và upload receipt nếu user gửi support.

---

## 12. Multi-user pilot model

### 12.1. Pilot cohort

Minimum final cohort:

- ít nhất 2 distinct OAuth owners;
- ít nhất 3 paired devices;
- ít nhất 1 AutoCAD Mechanical/Full R25 device;
- ít nhất 1 device thuộc một older Full family đã certified;
- ít nhất 1 real AutoCAD LT 2024+ device cho compatibility evidence;
- owner/device mapping thật, không reuse một owner để giả isolation;
- named support owner và operator.

Nếu hardware/license availability không đáp ứng, final report phải giảm scope
công khai và giữ Customer Pilot NO-GO; không tự hạ gate.

### 12.2. Pilot feature profile

Pilot bắt đầu theo staged profile:

1. pairing/device lifecycle;
2. read-only observe/query/scene/workflow;
3. trusted approval and low-risk create/modify flows;
4. rollback/recovery drills;
5. limited higher-risk operations only after prior stages stable.

Flags default off ngoài cohort. LT write vẫn off trừ một future accepted scope
có independent evidence.

### 12.3. Isolation evidence

Test xuyên:

- device list/detail;
- release/download selection;
- jobs/programs/previews/consents/receipts;
- workflows/scenes/resources;
- support bundles;
- update/rollback receipts;
- telemetry aggregate dimensions;
- Portal direct URL guesses;
- revoked/replaced session.

Cross-owner IDs trả `not_found` trước payload lookup/dispatch.

### 12.4. Required drills

- pair → observe → unpair/revoke → blocked reconnect → re-pair;
- Agent restart và Gateway restart;
- AutoCAD restart/Host reload;
- busy/modal block;
- network disconnect/reconnect;
- update available trong active job;
- package tamper/signature failure;
- update health failure → rollback;
- unknown outcome → no retry → recovery evidence;
- rollback conflict;
- support bundle generation/redaction;
- release revoke/kill switch;
- LT device never loads Managed Host;
- older family exact package selection.

### 12.5. Pilot success metrics

Retain aggregate, privacy-bounded metrics:

- install/upgrade/rollback success rate;
- signature/compatibility failures;
- pairing/reconnect success;
- runtime readiness;
- read/write workflow success;
- latency percentiles by bounded operation class;
- `outcome_unknown`/recovery/rollback counts;
- duplicate-effect count, target zero;
- cross-owner leak count, target zero;
- crash/disconnect rate;
- telemetry exporter drops/errors;
- support bundle count and resolution time;
- version adoption by cohort.

No raw drawing content or high-cardinality owner/device IDs in aggregate
telemetry.

---

## 13. Security và threat model delta

### 13.1. New assets

- production signing key and certificate;
- release manifests/catalog;
- build provenance/SBOM;
- distribution artifacts;
- installer/updater privileges;
- install/update/rollback receipts;
- support bundles;
- pilot cohort configuration;
- release revoke/minimum-version policy.

### 13.2. Threats phải address

- compromised build runner;
- stolen/misused signing key;
- unsigned/tampered artifact;
- manifest/artifact mix-and-match;
- rollback to vulnerable release;
- wrong AutoCAD family package;
- LT loading Managed Host;
- path traversal/symlink/junction attack;
- update during effect-bearing job;
- partial install leaving incompatible Agent/Host;
- malicious local file replacing previous-known-good;
- untrusted download redirect;
- support bundle secret/drawing leakage;
- cross-owner release/support record access;
- release status cache serving revoked artifact;
- model/user trying to choose executable/path/version outside policy;
- telemetry outage blocking CAD work;
- installer elevation abuse.

### 13.3. Mandatory invariants

```text
TLS != artifact trust
signed != compatible
compatible != supported
installed != healthy
previewed != approved
updated != safe to retry write
support bundle != raw machine dump
```

### 13.4. Incident response inputs

Document:

- certificate/key compromise;
- vulnerable release revoke;
- bad rollout stop;
- exact cohort/device exposure lookup;
- rollback target;
- minimum-version bump;
- communication owner;
- evidence preservation;
- post-incident credential/package rotation.

---

## 14. Telemetry và privacy soak

Phase 5 telemetry remains fail-open. Phase 11 extends only bounded dimensions
needed for distribution/pilot:

- component/family/channel;
- install/update/rollback state;
- safe error code;
- operation class;
- success/failure;
- bounded latency bucket;
- reconnect/runtime health;
- exporter drop/error counters.

Không thêm:

- raw command/program/scene payload;
- drawing name/path/content;
- owner email/subject;
- device ID as high-cardinality dimension;
- token/certificate secret;
- unrestricted exception text.

Required soak:

- 3–7 consecutive days;
- collector outage/token rotation fail-open;
- Agent/Host/Gateway restart;
- at least one update and rollback event;
- at least one revoke/re-pair;
- privacy review of stored aggregate;
- retained summary with exact release/cohort/time window.

---

## 15. Implementation slices

### Slice 11.0 — Baseline, ADR và threat model delta

Deliverables:

- verified Phase 10 baseline report;
- release/distribution boundaries ADR;
- installer scope decision;
- release unit/versioning decision;
- compatibility/family support matrix;
- signing/key custody interface decision;
- threat model delta;
- pilot cohort and evidence plan.

Exit:

- no code beyond contracts/fixtures until release truth, authority and update
  safe-window boundaries accepted;
- no unresolved question về lab signing vs production trust;
- no ambiguous support claim policy.

### Slice 11.1 — Strict release contracts và validators

Add:

- release/artifact/compatibility/selection contracts;
- install/update/rollback/support/pilot evidence contracts;
- canonical digest helpers;
- bounded parsers;
- golden JSON vectors;
- conflicting duplicate and mix-and-match rejection.

Exit:

- strict extra-forbid;
- finite/bounded values;
- deterministic digest;
- unknown family/component rejected;
- revoked/unsupported state explicit.

### Slice 11.2 — Unified build, SBOM và provenance pipeline

Refactor/productize existing Phase 5 scripts:

- exact clean build root;
- component builds;
- dependency lock verification;
- SBOM generation;
- provenance generation;
- pre-sign tests/scans;
- immutable staging layout;
- machine-readable build report.

Exit:

- same source/toolchain produces reproducible semantic manifest;
- dirty/mutable/unlocked input fails;
- no duplicate Phase 5/Phase 11 pipeline divergence;
- all component artifacts accounted for.

### Slice 11.3 — Production signing và publication gates

Add:

- CA signer integration;
- trusted timestamp;
- post-sign verification;
- signer allowlist/policy;
- certificate rotation/expiry checks;
- release candidate validator;
- immutable publication record;
- revoke/supersede behavior.

Exit:

- unsigned/self-signed/missing timestamp/tampered artifact fails closed;
- signature and manifest mix-and-match rejected;
- private key never exported/logged;
- publication cannot be partial.

### Slice 11.4 — Runtime-aware installer and family packages

Add:

- product/runtime probe;
- exact family selector;
- Agent package;
- R25 package;
- older family package path selected for certification;
- LT compatibility package;
- side-by-side package handling;
- install receipts;
- clean uninstall behavior;
- typed errors and UI projection.

Exit:

- exact family component selection;
- unsupported/ambiguous host blocked before mutation;
- LT never installs/loads Managed Host;
- no terminal/.env/manual copy onboarding;
- existing package drift detected.

### Slice 11.5 — Update, safe window and rollback

Add:

- persisted update state machine;
- download/verify/stage;
- safe-window guard;
- activation health gate;
- previous-known-good rollback;
- anti-rollback/minimum-version policy;
- emergency revoke/rollback;
- restart/reconcile.

Exit:

- no activation during effect-bearing/unresolved job;
- crash at every update transition resolves safely;
- failed health rolls back exact package or enters needs_support;
- duplicate update/rollback request is idempotent;
- no CAD effect created by updater.

### Slice 11.6 — Portal/Agent release UX and distribution

Add:

- durable release catalog;
- download/update pages;
- device version/readiness state;
- known issues/support status;
- update blockers/actions;
- recent-auth/audit for trusted mutations;
- immutable authorized artifact delivery.

Exit:

- UI renders server/Agent truth;
- uncertified family hidden or explicitly unsupported;
- revoked release cannot be newly downloaded/activated;
- direct URL/IDOR tests green;
- accessibility/state copy tests green.

### Slice 11.7 — Diagnostics, telemetry and support operations

Add:

- strict support-bundle contract;
- allowlisted collector/redaction;
- bundle preview/digest/upload receipt where applicable;
- install/update telemetry dimensions;
- operator runbooks;
- support ownership and escalation matrix.

Exit:

- default bundle contains no secrets/raw drawing;
- bounded size/time window;
- collector/upload outage fail-open for CAD work;
- support can correlate release/device/job without unsafe payload.

### Slice 11.8 — Cross-family clean-VM certification

Add:

- automated clean-VM harness;
- R25 install/upgrade/rollback evidence;
- one older Full family real load/smoke;
- real LT 2024+ install/read compatibility evidence;
- SECURELOAD/trusted location checks;
- protocol rolling-compatibility matrix;
- exact release candidate conformance.

Exit:

- R25 pilot support proven;
- at least one older Full family support proven;
- LT compatibility proven without Managed Host;
- unsupported families not claimed;
- all signed package sets pass Phase 6–10 regressions.

### Slice 11.9 — Limited multi-user pilot and final evidence

Add:

- cohort configuration;
- staged flags;
- onboarding sessions;
- revoke/re-pair and incident drills;
- 3–7 day telemetry soak;
- multi-owner isolation matrix;
- update/rollback in cohort;
- final security/operations review;
- Customer Pilot GO/NO-GO report.

Exit:

- all Phase 11 GO gates met;
- no duplicate CAD effect;
- no cross-owner leak;
- support/rollback owner available;
- pilot evidence retained;
- Phase 12 remains separately gated.

---

## 16. Testing matrix

### 16.1. Contract tests

- strict unknown-field rejection;
- canonical digest;
- artifact/manifest mix-and-match;
- duplicate immutable insert;
- revoked/superseded state;
- version/range parsing;
- invalid family/channel/status;
- size/depth/count limits;
- receipt correlation;
- schema snapshots.

### 16.2. Signing/supply-chain tests

- unsigned artifact;
- wrong signer;
- invalid chain;
- missing timestamp;
- modified signed byte;
- modified manifest;
- stale/missing SBOM;
- provenance source mismatch;
- partial release;
- duplicate artifact path;
- unsafe relative path;
- production mode with lab certificate;
- certificate expiry/rotation warning;
- malware review missing/failed.

### 16.3. Probe/installer tests

- no AutoCAD;
- R25 Full;
- one older certified Full family;
- LT 2024+;
- multiple side-by-side releases;
- unsupported release;
- ambiguous registry/install evidence;
- wrong architecture;
- insufficient disk;
- AutoCAD running;
- Agent active job;
- unresolved outcome;
- existing bundle drift;
- clean install;
- upgrade;
- uninstall;
- interrupted install/restart;
- path traversal/junction/symlink defense.

### 16.4. Update/rollback state tests

Fault inject before/after every transition:

- download;
- verify;
- stage;
- receipt creation;
- package move;
- activation;
- health check;
- receipt persist;
- rollback move/restore;
- post-rollback health.

Verify:

- idempotent repeat;
- no partial compatible claim;
- exact previous-known-good restore;
- drift conflict safe;
- revoked version blocked;
- minimum version enforced;
- no update during active effect-bearing job;
- no write retry side effect.

### 16.5. Rolling compatibility tests

- Gateway N with Agent N-1/N;
- Agent N with Host N-1/N where allowed;
- Agent/Host mismatch outside range;
- package/registry change invalidates preview/consent;
- read-only degradation explicit;
- restart/reconnect retains durable truth;
- feature flags disable safely.

### 16.6. Portal/Agent UI tests

- release catalog support claims;
- direct URL/IDOR;
- CSRF/recent-auth;
- download authorization;
- update blocker/action mapping;
- revoked/superseded copy;
- rollback confirmation;
- no arbitrary version/path input;
- keyboard/focus/no color-only state;
- Vietnamese copy;
- no secret/path leakage.

### 16.7. Support bundle tests

- allowlist only;
- deterministic redaction;
- no token/private key;
- no raw drawing/program/scene;
- full path reduction;
- size/time/event budgets;
- malicious filename/path;
- missing files;
- concurrent log writes;
- local preview;
- exact digest;
- upload failure does not block Agent/CAD.

### 16.8. Multi-owner security tests

- cross-owner release selection/download metadata;
- device/update receipt IDOR;
- support bundle IDOR;
- pilot cohort config isolation;
- revoked device reconnect;
- replaced session terminal result rejection;
- owner change/email change stability;
- telemetry privacy/cardinality.

### 16.9. End-to-end tests

1. Build exact release candidate from clean commit.
2. Generate SBOM/provenance and sign/timestamp all required artifacts.
3. Publish immutable internal/pilot manifest.
4. Clean-install R25 and pair Owner A.
5. Run read-only observe/query/scene/workflow.
6. Run trusted approved write/validate/recovery path.
7. Stage update during active job and prove activation blocked.
8. Activate in safe window and rerun Phase 6–10 conformance.
9. Force health failure and prove exact rollback.
10. Install older Full family and prove exact family selection/load.
11. Install LT package and prove Managed Host absent and read compatibility.
12. Pair Owner B/C devices and prove isolation.
13. Revoke/re-pair, disconnect/reconnect and Gateway restart.
14. Generate redacted support bundle.
15. Complete telemetry soak and incident rollback drill.

---

## 17. Clean-VM và live certification evidence

### 17.1. R25 required matrix

- clean Windows VM;
- supported AutoCAD Mechanical/Full 2025+ install;
- clean product install;
- Agent onboarding/pairing;
- Host package load under security settings;
- observe/query/scene;
- prepare/preview/trusted approval/commit/validate;
- recovery/rollback drill;
- Agent update;
- Host update requiring safe AutoCAD restart;
- bad update health rollback;
- clean uninstall/reinstall;
- exact package hashes/signatures/timestamps retained.

### 17.2. Older Full family

Certify at least one real older family, preferably R24 first because it covers a
large release range and differs from R25 runtime.

Evidence:

- exact AutoCAD product/release;
- exact package family and `PackageContents.xml` range;
- runtime load/handshake;
- supported read/write capability subset;
- unsupported operations fail honestly;
- install/upgrade/rollback;
- no R25 assembly selected;
- live smoke and retained logs/artifacts.

R22/R23/R24 families without evidence remain hidden/uncertified.

### 17.3. Real AutoCAD LT 2024+

Evidence:

- product is LT, not Full fallback;
- installer selects only LT compatibility package;
- Managed Host absent/not loaded;
- pairing/presence/read observe/query works for declared subset;
- capability manifest identifies LT primary compatibility;
- unsupported Managed features fail `capability_missing`;
- no silent Managed fallback;
- LT write remains disabled unless separately approved;
- install/upgrade/rollback and support bundle work.

### 17.4. Evidence artifact requirements

Mỗi artifact ghi:

- source/release commit;
- release ID/manifest digest;
- artifact versions/hashes;
- signer/timestamp evidence;
- SBOM/provenance refs;
- OS/AutoCAD product/release/vertical;
- device/owner-safe IDs;
- install/update/rollback receipts;
- commands/test cases;
- failures/retests;
- operator/date;
- support status decision.

Không dùng headless-only hoặc compile-only evidence để claim family support.

---

## 18. Feature flags và rollout

Dự kiến additive flags/policies:

```text
phase11_release_catalog_enabled
phase11_portal_download_enabled
phase11_agent_update_enabled
phase11_automatic_stage_enabled
phase11_automatic_activate_enabled
phase11_support_bundle_enabled
phase11_pilot_write_profile_enabled
phase11_older_family_support_enabled
phase11_lt_compat_pilot_enabled
```

Rules:

- default off ngoài internal/pilot cohort;
- download có thể bật trước activation;
- automatic activation mặc định off trong early pilot;
- family flag chỉ bật sau exact certification;
- disable update không xóa receipts/evidence;
- revoke release chặn new download/activation nhưng giữ audit;
- no destructive DB downgrade;
- existing installed release không tự bị xóa;
- emergency kill switch không fake-cancel effect already committed;
- rollback target phải explicit và signed.

---

## 19. Suggested code/file scope

Expected additions/changes:

```text
packages/contracts/src/autocad_contracts/phase11_contracts.py
packages/contracts/tests/test_phase11_*.py

services/gateway/src/autocad_gateway/releases/
  models.py
  service.py
  repository.py
  compatibility.py
  rollout.py
  public_projection.py
services/gateway/src/autocad_gateway/infrastructure/sqlite/migrations/
services/gateway/src/autocad_gateway/infrastructure/sqlite/
services/gateway/src/autocad_gateway/composition.py
services/gateway/src/autocad_gateway/portal_api.py
services/gateway/tests/phase11/

apps/desktop_agent/src/autocad_desktop_agent/updates/
  models.py
  probe.py
  selector.py
  verifier.py
  state_machine.py
  installer.py
  rollback.py
  diagnostics.py
apps/desktop_agent/tests/phase11/

apps/web_portal/
  app/downloads/
  app/devices/*/updates/
  app/support/
  components/releases/
  tests/

native/autocad_managed_host/packaging/
  R22/
  R23/
  R24/
  R25/

scripts/build-phase11-release.ps1
scripts/sign-phase11-release.ps1
scripts/validate-phase11-release.py
scripts/test-phase11-install-rollback.ps1
scripts/test-phase11-clean-vm.ps1
scripts/test-phase11-conformance.py
scripts/validate-phase11-pilot-evidence.py

.github/workflows/phase11-release-security.yml
.github/workflows/phase11-packaging.yml
.github/workflows/phase11-conformance.yml

docs/architecture/Phase-11.md
docs/architecture/phase11-release-boundaries-adr.md
docs/architecture/phase11-compatibility-matrix.md
docs/architecture/phase11-pilot-runbook.md
docs/architecture/phase11-incident-rollback.md
docs/architecture/evidence/
docs/security/phase11-threat-model.md
docs/security/phase11-security-review.md
docs/operations/phase11-signing-key-runbook.md
docs/operations/phase11-support-runbook.md
```

Không tạo toàn bộ file máy móc nếu existing structure có vị trí tốt hơn. Mọi
deviation phải giải thích.

Expected unchanged về semantics:

```text
Phase 7 trusted approval authority
Phase 7 recovery/rollback CAD semantics
Phase 8 CAD Program compiler/execution authority
Phase 9 workflow state/retry authority
Phase 10 scene read-only/inference authority
Managed Host write operation registry except additive packaging metadata
public MCP write tools
```

---

## 20. Subagent review plan

Dùng tối đa năm review streams:

1. **Release contracts/supply-chain reviewer**
   - manifest, digests, SBOM, provenance, signing, publication, revocation.

2. **Installer/update/rollback reviewer**
   - probe, family selection, state machine, safe window, receipts, crash safety.

3. **Runtime/cross-family reviewer**
   - R22–R25 packages, LT separation, Agent/Host protocol compatibility,
     real AutoCAD evidence.

4. **Security/identity/support reviewer**
   - key custody, owner isolation, Portal auth, support bundle redaction,
     incident response.

5. **Pilot/operations/UI reviewer**
   - onboarding, staged rollout, telemetry soak, runbooks, accessibility,
     customer evidence quality.

Integration owner:

- resolves release schema/version conflicts;
- owns immutable publication and compatibility truth;
- prevents duplicate packaging pipelines;
- runs full Phase 0–11 suite;
- validates exact signed RC;
- decides Engineering GO, Release Candidate GO và Customer Pilot GO.

Subagent findings phải được ghi lại trước integration. Không merge blind
recommendations.

---

## 21. Start gates

GO to implementation only when:

- `main` contains PR #14;
- Phase 0–10 baseline green;
- release unit/versioning ADR accepted;
- production signing interface and key custody owner identified;
- trusted timestamp endpoint identified or explicit external blocker recorded;
- installer per-user/per-machine scope accepted;
- family detection/selection rules accepted;
- LT separation accepted;
- immutable release/compatibility contracts accepted;
- update safe-window rules accepted;
- rollback/anti-rollback policy accepted;
- Portal distribution trust boundary accepted;
- support bundle redaction policy accepted;
- pilot cohort minimum and owner isolation matrix accepted;
- no Phase 6–10 authority is moved into installer/updater.

---

## 22. Phase 11 Repository Engineering GO

GO only when all are true:

1. Release/artifact/compatibility/install/update/rollback contracts strict và
   versioned.
2. Canonical release digest deterministic và server-validated.
3. Release/artifacts immutable after signing/publication.
4. Conflicting duplicate/mix-and-match rejected.
5. Unified build pipeline starts from clean exact commit.
6. Locked dependency/toolchain evidence retained.
7. SBOM và provenance generated for every required component.
8. Malware review gate machine-readable và fail-closed.
9. Production signing mode requires CA trust and trusted timestamp.
10. Self-signed/lab artifact cannot enter pilot channel.
11. Post-sign hashes/signatures/timestamps validated.
12. Private key/secret không exported/logged.
13. Runtime-aware probe bounded và read-only.
14. Exact family selection deterministic.
15. Unsupported/ambiguous family blocked before mutation.
16. LT never installs/loads Managed Host.
17. Installer onboarding requires no terminal/`.env`/manual package copy.
18. Install receipts exact, durable và restart-safe.
19. Update state machine persisted and idempotent.
20. No update activation during effect-bearing/unresolved job.
21. Agent/Host/package change invalidates bound preview/consent as required.
22. Activation health gate implemented.
23. Previous-known-good rollback restores exact verified digest.
24. Drift/conflict stops blind rollback.
25. Revoked/vulnerable release cannot be newly activated.
26. Minimum safe version/anti-rollback policy enforced.
27. Portal release/download/update records owner-safe and IDOR-tested.
28. Support bundle strict, bounded and redacted.
29. Telemetry remains fail-open and privacy-bounded.
30. Phase 6–10 conformance passes on exact packaged candidate.
31. All flags default off outside explicit internal/pilot profile.
32. Threat model/security review has no open critical/high blocker.
33. Rollback/incident/support runbooks committed.
34. CI and validators cover release security and evidence.

Repository Engineering GO does **not** by itself permit customer distribution
when external production signing/certification/pilot inputs are missing.

---

## 23. Phase 11 Release Candidate GO

GO only when Repository Engineering GO plus:

1. Exact RC artifacts are CA-signed and trusted-timestamped.
2. Signing key custody/owner/rotation/revocation procedure approved.
3. SBOM/provenance/malware review approved for exact RC.
4. R25 clean install/upgrade/rollback on clean VM passed.
5. R25 live AutoCAD load/observe/write/recovery/scene regressions passed.
6. One older Full family real load/smoke and package selection passed.
7. Real LT 2024+ compatibility install/read regression passed.
8. Signed RC passes Phase 6–10 conformance.
9. Portal download and Agent verification use immutable RC artifact refs.
10. Incident revoke and previous-known-good rollback drill passed.
11. Support bundle/privacy review passed.
12. Named pilot support owner and incident escalation path exist.

Uncertified families remain excluded from RC support manifest.

---

## 24. Phase 11 Customer Pilot GO

GO only when Release Candidate GO plus:

1. Explicit approved pilot cohort exists.
2. At least 2 distinct owners and 3 devices onboarded.
3. Owner isolation matrix passes across device, release, job, workflow, scene,
   support and receipt records.
4. Public OAuth lifecycle succeeds on pilot tenant.
5. Revoke/re-pair drill passes.
6. Busy/modal and disconnect/reconnect drills pass.
7. Unknown-outcome drill proves no blind retry/duplicate effect.
8. Update during active job is blocked, then succeeds in safe window.
9. Failed-update health rollback succeeds.
10. LT device never loads Managed Host.
11. Older Full device receives exact certified family package.
12. 3–7 day telemetry soak completes with privacy review.
13. Duplicate CAD effect count is zero.
14. Cross-owner leak count is zero.
15. No unresolved critical/high security or pilot-blocking incident.
16. Support ownership and incident rollback are exercised, not paper-only.
17. Final report records exact cohort, releases, metrics, failures and scope.
18. Customer communication states certified and unsupported families honestly.

Phase 11 final status must state separately:

- Repository Engineering GO/NO-GO;
- Release Candidate GO/NO-GO;
- Customer Pilot GO/NO-GO;
- Phase 12 Production GO remains separate.

---

## 25. NO-GO conditions

NO-GO nếu:

- release chỉ là ZIP/script không immutable manifest;
- production pilot dùng self-signed hoặc unsigned artifact;
- trusted timestamp thiếu nhưng vẫn publish;
- private key/PFX/password xuất hiện trong repo/log/artifact;
- SBOM/provenance/malware gate bị bỏ qua;
- manifest/artifact có thể mix-and-match;
- mutable `latest` URL là nguồn truth duy nhất;
- installer chọn family bằng filename/guess/fallback;
- LT load Managed Host;
- unsupported family được quảng cáo supported;
- installer/update chạy arbitrary path/URL/command;
- update activate trong active write/unknown outcome;
- partial Agent/Host mismatch được gọi healthy;
- rollback dùng arbitrary old version hoặc modified backup;
- vulnerable/revoked release vẫn có thể activate;
- install/update receipt chỉ nằm trong log text;
- Portal/browser quyết định compatibility/support;
- cross-owner download/update/support records leak tồn tại;
- support bundle chứa token/private key/raw drawing/full unsafe path;
- telemetry outage block CAD work;
- pilot chỉ dùng một synthetic owner;
- no real older family evidence nhưng claim older support;
- no real LT evidence nhưng claim LT certification;
- Phase 6–10 conformance không chạy trên exact signed RC;
- duplicate CAD effect xảy ra;
- unresolved critical/high security finding;
- support/incident owner không tồn tại;
- Customer Pilot được gọi GO chỉ vì clean-VM installer passed;
- Phase 12 production/scale được gọi GO từ limited pilot evidence.

---

## 26. Rollback, revoke và disable strategy

Nếu Phase 11 release/pilot có sự cố:

1. stop new rollout/activation;
2. mark release revoked hoặc rollout-paused trong durable catalog;
3. disable Portal download cho affected release;
4. disable automatic staging/activation;
5. preserve manifests, receipts, support/audit evidence;
6. identify exact affected cohort/devices bằng safe IDs;
7. block new effect-bearing jobs nếu package integrity/compatibility uncertain;
8. do not auto retry unresolved writes;
9. activate explicit previous-known-good rollback only in safe window;
10. verify post-rollback runtime health and Phase 6–10 critical path;
11. keep unrecoverable device in `needs_support`/read-only state;
12. rotate/revoke certificate or signing policy if key compromise suspected;
13. bump minimum safe version when vulnerability requires;
14. do not delete bad release record or rewrite artifact in place;
15. communicate exact supported scope and next action;
16. produce incident report and recertify new release ID.

Gateway/Portal feature rollback must not destructively downgrade durable schema.
Existing jobs/programs/workflows/scenes/receipts remain source evidence.

---

## 27. Required deliverables

- `docs/architecture/Phase-11.md` updated with implementation evidence;
- release/distribution boundaries ADR;
- strict Phase 11 contracts/golden vectors;
- unified build pipeline;
- production signing/timestamp integration;
- SBOM/provenance/malware gates;
- immutable release catalog/repository;
- compatibility/family selection engine;
- runtime-aware installer;
- update/safe-window/rollback state machine;
- Portal download/update UX;
- Agent release/update UX;
- support bundle/redaction contract;
- telemetry soak tooling/report;
- clean-VM R25 evidence;
- older Full family certification evidence;
- real LT 2024+ compatibility evidence;
- multi-owner pilot evidence;
- threat model/security review;
- signing-key, support, pilot and incident runbooks;
- Phase 11 CI/conformance/evidence validators;
- final GO/NO-GO report.

---

## 28. Final implementation report

Final report phải ghi:

- baseline commit;
- implementation/final release commits;
- exact release ID/manifest digest;
- all artifact versions/hashes/signers/timestamps;
- SBOM/provenance/malware review refs;
- CI commands/runs/counts;
- clean-VM environments;
- real AutoCAD products/releases/verticals;
- owner/device-safe pilot cohort summary;
- install/update/rollback receipts;
- Phase 6–10 conformance on exact RC;
- telemetry soak window/metrics;
- revoke/re-pair, unknown-outcome and incident drills;
- support ownership;
- failures, fixes and recaptures;
- unsupported families/runtimes;
- open risks;
- Repository Engineering decision;
- Release Candidate decision;
- Customer Pilot decision;
- explicit Phase 12 handoff status.

Không dùng artifact được tạo trước final implementation head để certify release
mới nếu provenance không chứng minh exact ancestry và code equivalence theo
accepted policy. Không sửa tay retained evidence sau capture.

---

## 29. Handoff sang Phase 12

Phase 12 chỉ bắt đầu formal khi Phase 11 Customer Pilot GO hoặc có explicit
architecture decision ghi rõ reduced handoff.

Phase 12 nhận:

- signed release train và compatibility truth;
- certified family/support matrix;
- update/rollback state machine;
- pilot SLI baseline;
- incident/support evidence;
- multi-owner isolation evidence;
- capacity observations;
- known operational limits.

Phase 12 mới quyết định dựa trên load evidence:

- SLO/SLI và alerting production;
- quota/rate limit/subscription;
- backup/restore và retention;
- PostgreSQL/queue/multi-worker;
- scale tests;
- tenant-aware admin/support;
- ecosystem governance;
- signed skill/capability publication.

Không dùng limited pilot để biện minh sớm cho distributed infrastructure hoặc
marketplace.
