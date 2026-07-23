# Kế hoạch triển khai Phase 4 — Real Windows Agent C1, UI Lab và public E2E

> Trạng thái cập nhật 2026-07-23: gate 4.0 và phần code/local verification của 4.1–4.2 đã triển khai trong profile opt-in `phase4_c1`; happy path, AutoCAD failure matrix, protocol reconnect/restart và 10 mẫu latency đã chạy qua Gateway/WSS/Agent/AutoCAD Mechanical 2025 thật. VPS Gateway, Cloudflare và kết nối ChatGPT đã được operator thiết lập/kiểm tra. PR CI đã xanh; build standalone hosted được tách thành `standalone-release` chạy bằng `workflow_dispatch`, còn standalone trên VM sạch, hosted artifact, token/protocol-client evidence và rollback cutover vẫn `NO-GO`. Xem [Phase 4 C1 implementation evidence](./phase4-c1-implementation-evidence.md).
>
> Baseline branch: `main`
>
> Baseline commit: `2517ec80e95c88776bcbfb98e0b33078099ef444`
>
> Phạm vi: POC C1 read-only trên một máy Windows lab; không phải production multi-user.

## 1. Tóm tắt

Phase 4 chứng minh luồng AutoCAD thật đầu tiên trên nền Durable Gateway đã được hardening ở Phase 3.1:

```text
ChatGPT Web / MCP protocol client
    -> cad.kythuatvang.com
    -> VPS FastMCP Gateway
    -> outbound WSS
    -> Windows Desktop Agent
    -> narrow read-only CAD port
    -> Safe File IPC / COM
    -> packaged AutoLISP drawing-info
    -> AutoCAD thật
```

Phạm vi bị khóa:

- Một lab user, một máy Windows và một device được allowlist.
- Chỉ hỗ trợ operation đọc `observe` bằng package `autocad.lisp.drawing_info`.
- Không ghi bản vẽ, không chạy raw AutoLISP, không dùng `write_fixture` ngoài test Phase 3.1.
- Agent luôn chủ động kết nối outbound; máy người dùng không mở inbound port hoặc tunnel riêng.
- Tái sử dụng Auth0 hiện tại và public host `cad.kythuatvang.com` cho public E2E.
- Có Agent headless, sau đó mới bổ sung UI Lab PySide6, tray, hard pause và diagnostics.
- Artifact bàn giao là standalone folder; chưa làm installer, auto-update, production pairing, Portal hoặc production multi-user.
- Phase 4 chỉ đạt khi cùng một yêu cầu đọc chạy thành công bằng MCP protocol client và ChatGPT Web trên cùng bản vẽ AutoCAD thật.

Phase 4 không thay thế hoặc viết lại FastMCP facade, SQLite job store, state machine, reconnect protocol, simulator hay public contract đã có. Agent thật phải tuân thủ các invariant Phase 3.1 thay vì tạo một semantics riêng.

## 2. Điều kiện bắt đầu và baseline

Phase 4 bắt đầu trực tiếp từ `main` tại commit baseline nêu trên. Không dùng lại nhánh `agent/phase3-durable-gateway` làm nền.

Trước khi sửa code Phase 4:

1. Xác nhận các workflow tại baseline đều xanh, đặc biệt:
   - Phase 3.1 Durable Lifecycle Hardening;
   - FastMCP Phase 2 Gateway;
   - FastMCP Phase 0;
   - Phase 1.1 CAD Core Hardening;
   - root regression.
2. Cập nhật `phase3.1-durable-lifecycle-hardening-evidence.md` và master plan nếu chúng vẫn ghi `NO-GO pending hosted CI`.
3. Chụp và khóa schema snapshots hiện tại của:
   - public `cad.mcp/1.1` Phase 3 profile;
   - local `cad.mcp/1.0` profile;
   - shared `cad.agent/1` protocol.
4. Chạy lại Gateway, contracts, simulator, CAD Core, Phase 0, legacy tests, package builds, lockfile checks và `git diff --check`.
5. Mọi regression Phase 0–3.1 là điều kiện dừng.

## 3. Chiến lược triển khai theo gate

Phase 4 vẫn là một phase sản phẩm, nhưng được chia thành bốn gate để cô lập lỗi và tránh ghép COM, Qt, Auth0, WSS và public deployment cùng lúc.

### Phase 4.0 — Baseline, contract và migration delta

Mục tiêu:

- Khóa baseline Phase 3.1.
- Chốt public contract `cad.mcp/1.2` theo hướng additive.
- Chốt extension tương thích của `cad.agent/1`.
- Thêm migration Gateway `0003_phase4_c1.sql`.
- Chưa kết nối AutoCAD thật.

Gate hoàn thành:

- Contract snapshots chỉ thay đổi đúng các field đã duyệt.
- `phase3_poc` simulator cũ vẫn chạy đầy đủ.
- Local Phase 2 schema và semantics giữ nguyên.
- Migration `0001_phase3.sql` và `0002_phase31.sql` không bị sửa.

### Phase 4.1 — Headless Agent C1 + AutoCAD thật

Mục tiêu:

- Xây Agent core không có Qt.
- Kết nối outbound WSS tới Gateway.
- Thực thi đúng một package đọc trên AutoCAD thật.
- Chứng minh ledger, reconnect, duplicate và package mismatch trước khi thêm UI.

Gate hoàn thành:

- MCP protocol client chạy E2E qua Gateway, WSS, Agent, File IPC và AutoCAD thật.
- AutoCAD closed, no document, busy, modal và document switch trả structured error.
- Reconnect và duplicate không chạy command lần hai ngoài path `not_started` đã được reconcile.
- Command bị cấm không chạm backend.

### Phase 4.2 — UI Lab, DPAPI và standalone package

Mục tiêu:

- Bọc AgentCore đã ổn định bằng UI PySide6.
- Thêm tray, hard pause, diagnostics và DPAPI credential store.
- Tạo standalone folder có thể chạy trên Windows 11 VM sạch.

Gate hoàn thành:

- UI không gọi WSS, COM hoặc File IPC trực tiếp.
- UI thread không bị block khi mất mạng hoặc AutoCAD bận.
- Hard pause sống qua restart và chặn mọi remote command mới.
- Standalone artifact chạy được ngoài source tree.

### Phase 4.3 — VPS, Auth0 và ChatGPT Web cutover

Mục tiêu:

- Deploy profile `phase4_c1` lên VPS.
- Dùng public TLS/WSS qua Cloudflare Tunnel.
- Xác thực ChatGPT bằng Auth0 `autocad.read`.
- Chạy public E2E và thử rollback host.

Gate hoàn thành:

- MCP protocol client và ChatGPT Web đọc cùng bản vẽ thành công.
- Token sai hoặc thiếu scope không tạo DB row hay command.
- Agent đúng device, package và document.
- Rollback public host đã được thực hành.

Không được bắt đầu gate sau nếu gate trước chưa có bằng chứng xanh.

## 4. Public MCP contract `cad.mcp/1.2`

Public surface vẫn giữ đúng:

- 4 tools: `cad_list_devices`, `cad_observe`, `cad_query`, `cad_get_job`.
- 5 resources hiện có.
- 2 prompts hiện có.
- Không bổ sung tool ghi.
- Không expose primitive, File IPC command hay package executor trực tiếp.

Các thay đổi Phase 4 phải additive và chỉ bật trong profile `phase4_c1`.

| Thành phần | Delta Phase 4 |
|---|---|
| `DeviceInfo` | Thêm optional `runtime_state`, `document_name`, `last_seen_at`, `agent_version`, `package_summary`, `paused` |
| `cad_observe` | Trả summary drawing, `job_id` và `execution_evidence` |
| `cad_get_job` | Thêm optional `agent_version`, `command_id`, package ID/version/SHA-256 và runtime evidence |
| `cad_query` | Không giả lập entity detail từ summary-only observation |

### 4.1. Quy tắc `cad_query` cho C1

Package C1 chỉ tạo summary, không tạo entity snapshot chi tiết. Vì vậy:

- Không được trả `total=0` nếu drawing summary cho biết bản vẽ có entity.
- Yêu cầu query entity/detail trên snapshot C1 phải trả `capability_missing` hoặc một kết quả typed ghi rõ `detail_available=false`.
- `entity_count` của observation phải lấy từ drawing summary đã validate.
- Không tạo danh sách entity giả, handle giả hoặc geometry rỗng để làm như detail tồn tại.

Ưu tiên cho C1:

```text
cad_query(summary-only snapshot) -> capability_missing
```

Việc bổ sung query summary riêng chỉ được thực hiện nếu public schema được review và snapshot delta được duyệt.

### 4.2. Revision của observation C1

`document_revision` vẫn được trả để giữ contract, nhưng C1 chỉ có summary observation. Nó phải kèm evidence:

```json
{
  "revision_schema": "cad.revision/1",
  "revision_strength": "summary_only",
  "commit_safe": false
}
```

Revision có thể dùng identity cục bộ cộng summary chuẩn hóa để phát hiện thay đổi read-only trong C1. Full path chỉ tồn tại trong Agent và không được gửi lên Gateway.

Revision summary-only tuyệt đối không được dùng làm điều kiện an toàn cho write, preview, commit hoặc rollback ở Phase C2. Khi mở write, Agent phải tạo revision từ drawing/entity state đủ mạnh theo contract đã được duyệt.

## 5. Gateway–Agent protocol

Giữ protocol name `cad.agent/1` nếu thay đổi chỉ additive và Agent Phase 3 simulator vẫn parse được. Nếu cần field bắt buộc làm Agent cũ không thể kết nối, phải tăng protocol version thay vì âm thầm phá `cad.agent/1`.

### 5.1. Extension additive

Các field Phase 4 nên là optional trong shared models, nhưng profile `phase4_c1` có thể yêu cầu chúng bằng policy:

- `agent_version`;
- `runtime_state`;
- `document_name` dạng basename;
- `paused`;
- `current_command_id`;
- `packages` hoặc `package_manifest`;
- `package_manifest_hash`;
- observation `execution_evidence`.

Simulator Phase 3 tiếp tục được phép dùng capability-only hello. Real Agent C1 phải gửi đầy đủ evidence mà profile `phase4_c1` yêu cầu.

### 5.2. Capability và package provenance

Capability manifest và package manifest là hai khái niệm riêng:

```json
{
  "capabilities": ["observe"],
  "packages": [
    {
      "package_id": "autocad.lisp.drawing_info",
      "version": "3.3-c1",
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

- Capability trả lời Agent hỗ trợ operation nào.
- Package manifest chứng minh operation đó được cung cấp bởi package/version/hash nào.
- Gateway canonicalize và tự tính hash; không tin hash do Agent gửi mà không đối chiếu.
- Dispatch kiểm capability và package ngay trước khi gửi command.
- Package mismatch làm device/session `incompatible` hoặc capability bị tắt; không được dispatch rồi mới phát hiện.

### 5.3. Invariant Phase 3.1 bắt buộc giữ

Real Agent phải tương thích với state machine và reconcile policy hiện có:

- Chỉ `reconnect_pending + not_started` mới được requeue và redispatch.
- Evidence `started` không được tự chạy lại, kể cả command đọc.
- Terminal `succeeded`, `failed`, `cancelled`, `needs_attention` là bất biến.
- Exact duplicate terminal result là no-op; conflicting result bị reject/audit.
- Cancel intent phải sống qua disconnect/restart.
- ACK `rejected`, `duplicate`, `already_terminal` có policy rõ và có thể kích hoạt reconcile.
- Mọi Agent message phải khớp active session, device, job, command, payload hash và monotonic sequence.
- Wait timeout của MCP caller không terminalize durable job.
- Agent error text không được đi thẳng ra public MCP boundary.

Simulator và Real Agent headless phải dùng chung contract fixtures/failure matrix càng nhiều càng tốt.

## 6. Gateway profile và migration

### 6.1. Profile `phase4_c1`

Thêm profile độc lập `phase4_c1`; không biến `phase3_poc` thành production profile và không dùng fixture authenticator ngoài test.

Profile phải fail-fast nếu thiếu:

- SQLite path cố định;
- Auth0 issuer, audience và JWKS URI;
- public origin và allowed host;
- đúng một lab user/device mapping;
- lab device credential allowlist;
- `write_disabled=true`;
- required capability `observe`;
- required package ID/version/hash;
- request wait, job deadline, WSS payload và heartbeat bounds.

Composition root:

```text
profile phase4_c1
    -> FastMCP auth for human user
    -> durable Gateway services
    -> persistent SQLite
    -> real Agent WSS transport
    -> lab device authenticator
    -> write-deny policy
```

`phase3_poc` vẫn dùng fixture tokens và simulator. `local` vẫn giữ behavior Phase 2.

### 6.2. Migration `0003_phase4_c1.sql`

Không sửa `0001_phase3.sql` hoặc `0002_phase31.sql`.

Migration Phase 4 là `0003_phase4_c1.sql`, bổ sung tối thiểu dữ liệu cần cho C1, ví dụ:

- `devices.agent_version`;
- `devices.runtime_state`;
- `devices.document_name`;
- `devices.paused`;
- package manifest/hash hoặc bảng package session riêng;
- timestamp runtime/package update nếu cần.

Không lưu:

- full path DWG;
- token, private key hoặc DPAPI blob;
- full AutoLISP source;
- screenshot/base64;
- raw Agent error/stack trace.

Migration phải dùng runner ordered, immutable, checksummed và atomic đã có từ Phase 3.1. Restart và backup/restore phải giữ được dữ liệu Phase 3.1 lẫn Phase 4.

## 7. Windows Desktop Agent C1

Tạo package độc lập:

```text
apps/desktop_agent/
  pyproject.toml
  src/autocad_desktop_agent/
    core/
    protocol/
    ledger/
    packages/
    autocad/
    diagnostics/
    ui/
  tests/
```

Dùng Python 3.12 x64. UI dependency chỉ được đưa vào gate 4.2.

### 7.1. Thành phần Agent

- `AgentCore`: WSS, hello/welcome, heartbeat, reconnect, reconcile, pause và bounded command queue.
- Local SQLite ledger: ghi command trước ACK; ghi terminal evidence trước khi gửi result.
- Package verifier: kiểm package ID, version, SHA-256 và dispatcher-reported version.
- Read-only AutoCAD executor: cổng hẹp chỉ cho health/status/drawing-info cần thiết.
- Credential store: DPAPI theo Windows user ở gate 4.2; headless test có injectable credential provider.
- Diagnostics exporter: chỉ xuất metadata đã redaction.
- UI adapter: chỉ nhận state snapshot và gửi user intent; không chứa network/COM logic.

### 7.2. Read-only boundary

Không truyền full `SafeFileIPCBackend` cho command router.

Agent C1 phải phụ thuộc vào `CadReadPort` hoặc một port nhỏ hơn, ví dụ:

```text
DrawingInfoExecutor
    - inspect_runtime()
    - health()
    - execute_drawing_info_package()
```

Implementation có thể bọc `SafeFileIPCBackend(allow_execute_lisp=False)`, nhưng router chỉ nhìn thấy read-only interface. Không có generic `call()`, create, modify, erase, save, plot, open file hoặc execute_lisp trên dependency của router.

Test bắt buộc chứng minh các command sau bị từ chối trước khi backend được gọi:

- `detail`;
- preview image;
- `write_fixture`;
- raw LISP;
- unknown package;
- package/hash mismatch;
- expired deadline;
- wrong session/device/job/command/payload hash;
- paused Agent.

### 7.3. Command được phép

Chỉ chấp nhận:

- kind: `observe`;
- effect class: `read`;
- observation level: `summary`;
- package ID: `autocad.lisp.drawing_info`;
- package/version/hash đúng manifest active.

Không có write command nào được ghi nhận là accepted hoặc được đưa tới File IPC.

### 7.4. Local ledger

Ledger lưu:

- `command_id`, `job_id`, `idempotency_key`, payload hash;
- state `received`, `accepted`, `started`, terminal;
- result hoặc safe error;
- package ID/version/hash;
- Agent/session identity;
- sequence và timestamps;
- durable cancel intent nếu nhận cancel.

Semantics:

- Cùng command identity và cùng payload hash: trả evidence đã lưu hoặc reconcile theo state.
- Cùng ID nhưng payload khác: `replay_payload_mismatch`.
- `not_started`: Gateway có thể redispatch theo Phase 3.1.
- `started`: không tự thực thi lần hai sau restart; reconcile báo started/known terminal.
- Terminal result được persist trước khi gửi về Gateway.
- Corrupt hoặc contradictory ledger evidence làm job `needs_attention`, không đoán và không retry.

### 7.5. AutoLISP package `3.3-c1`

Nâng dispatcher/package từ `3.2` lên `3.3-c1` theo kiểu additive và giữ reliability overrides hiện có.

Package drawing-info trả bounded JSON gồm:

- document basename, không có full path;
- `entity_count`;
- danh sách layer tối đa 256 phần tử;
- `layer_count`;
- cờ `truncated`;
- dispatcher version;
- package ID/version;
- optional drawing metadata cần cho summary revision.

Giới hạn:

- tối đa 255 ký tự cho mỗi layer name;
- escape JSON đúng cho quote, backslash và control characters được hỗ trợ;
- reject hoặc truncate có cờ rõ ràng;
- không đọc file ngoài IPC/package path;
- không eval raw code;
- không thay đổi drawing, sysvar hoặc selection.

## 8. UI Agent Lab

Chỉ bắt đầu sau khi Agent headless E2E xanh.

UI dùng PySide6 Widgets. Pin `PySide6==6.11.1`; pin cả toolchain build cần thiết để artifact có thể tái lập.

Qt main thread chỉ vẽ giao diện. `AgentCore` chạy trong background thread riêng, có asyncio loop và COM initialization phù hợp. Hai phía giao tiếp bằng queue thread-safe và Qt queued signals.

Widget không được:

- mở WSS;
- gọi File IPC;
- gọi COM;
- đọc token trực tiếp;
- mutate job state ngoài việc gửi user intent.

Màn hình chính tiếng Việt hiển thị:

- thiết bị;
- kết nối máy chủ;
- trạng thái AutoCAD;
- tên bản vẽ basename;
- tác vụ hiện tại;
- Agent/package version;
- trạng thái pause/incompatible;
- `Thử lại`, `Tạm dừng`, `Tiếp tục`, `Chẩn đoán`, `Trợ giúp`.

Không đưa vào Phase 4:

- nút cho phép ChatGPT chỉnh sửa;
- risk mode;
- production pairing/login;
- Portal;
- installer hoặc auto-update.

Thứ tự ưu tiên UI:

```text
paused
-> incompatible/package mismatch
-> gateway offline/connecting
-> AutoCAD closed
-> no document
-> modal/busy
-> remote job running
-> ready
```

Hành vi bắt buộc:

- Hard pause chặn mọi command mới, kể cả command đọc.
- Heartbeat và chẩn đoán cục bộ vẫn hoạt động khi paused.
- Pause state được persist qua restart.
- Hard pause không gửi `ESC` hoặc can thiệp command thủ công của user.
- Đóng cửa sổ thu nhỏ xuống tray.
- Tray có server/AutoCAD state, open, pause/resume, diagnostics và Exit.
- Exit khi có command đang chạy phải hỏi xác nhận và tạo structured terminal/cancel evidence theo state thực tế; không báo failed giả nếu outcome chưa biết.
- Manual Retry hủy backoff hiện tại và kết nối lại ngay.

Diagnostics chỉ chứa:

- Agent/Windows/AutoCAD version;
- device ID đã rút gọn;
- capability/package manifest hash;
- heartbeat/job/command/correlation IDs;
- safe error code;
- timestamp và redaction report.

Diagnostics không chứa token, private key, DPAPI blob, full path, drawing content, screenshot, raw AutoLISP hoặc raw Agent/Gateway stack trace.

Dùng `pyside6-deploy`/Nuitka để tạo standalone folder. One-file chỉ là benchmark tùy chọn, không phải artifact bàn giao. Trước pilot rộng hơn phải kiểm tra nghĩa vụ Qt LGPL/commercial.

## 9. Auth0, VPS và public cutover

### 9.1. Human OAuth

Gateway dùng FastMCP `RemoteAuthProvider` và `JWTVerifier` nếu POC Auth0 hiện có tương thích.

Mỗi request phải kiểm:

- RS256 signature;
- issuer;
- audience;
- expiry/not-before;
- subject;
- scope `autocad.read`.

Adapter Auth0 có thể hợp nhất `scope`, `scp` và `permissions`, nhưng identity lấy từ validated `(iss, sub)`, không lấy từ tool args hoặc `client_id`.

- Lab `sub` phải map đúng lab device.
- Token sai/thiếu scope trả 401/challenge và không tạo job, command hoặc DB row.
- Gateway tiếp tục từ chối mọi write dù token có `autocad.write`.
- Tool metadata và runtime challenge phải tương thích ChatGPT OAuth.

Đây là lab user authentication, chưa phải production device pairing hay two-user isolation. Production pairing, asymmetric device key, rotation và revoke thuộc Phase 5.

### 9.2. Device credential C1

- Một lab credential map đúng một device.
- Credential không hard-code trong source, manifest, CLI history hoặc log.
- UI build lưu credential bằng Windows DPAPI theo current Windows user.
- Revoke credential phải ngắt/reject reconnect của device.
- Không gọi credential này là production pairing.

### 9.3. VPS deployment

- Một Gateway worker.
- SQLite persistent volume.
- Gateway bind loopback/private interface.
- Cloudflare Tunnel terminate TLS/WSS.
- Public routes:
  - `/mcp`;
  - `/agent/ws`;
  - `/healthz`;
  - `/readyz`;
  - protected-resource metadata.
- Trusted host/origin và forwarded-header policy fail closed.

Cutover:

1. Dựng Gateway VPS và kiểm nội bộ.
2. Kết nối Agent lab bằng outbound WSS.
3. Xác nhận package/capability/runtime state trước public traffic.
4. Dừng connector cũ rồi chuyển hostname; không để hai Gateway cùng nhận traffic.
5. Kiểm `/readyz` 200, metadata 200, `/mcp` không token 401 và Agent online.
6. Refresh metadata app trong ChatGPT, cấp token mới và chạy E2E trong conversation mới.
7. Thử rollback về connector/backend cũ.

Rollback:

- dừng Agent;
- revoke lab credential;
- chuyển Cloudflare connector về dịch vụ trước;
- khởi động launcher cũ nếu cần;
- không xóa SQLite, config hoặc evidence artifacts.

## 10. Phân phối package AutoLISP cho máy lab

- Standalone Agent chứa signed/checksummed manifest và bản `mcp_dispatch.lsp` versioned.
- Script lab chỉ sao chép package vào thư mục cố định dưới `%LOCALAPPDATA%\Kythuatvang\AutoCADAgent\packages\...`.
- Hướng dẫn operator thêm đúng thư mục vào AutoCAD Support File Search Path/TRUSTEDPATHS.
- Script không tự sửa AutoCAD profile và không dùng path máy phát triển.
- Khi khởi động, Agent đối chiếu file hash, manifest version và dispatcher-reported version.
- Sai bất kỳ giá trị nào chuyển device sang `incompatible`, tắt capability và không chạy command.
- Artifact phải có SHA-256 độc lập cho Agent build và từng package.

## 11. Kế hoạch kiểm thử

### 11.1. Regression và contract

- Toàn bộ Phase 0–3.1 regression.
- Legacy File IPC/CAD Core regression.
- Schema snapshots cho local, Phase 3 và Phase 4 profile.
- Contract tests cho `cad.mcp/1.2` và additive `cad.agent/1` fields.
- Strict validation, `extra=forbid`, payload/message/depth/list/string bounds.
- Simulator Phase 3 vẫn kết nối và hoàn thành failure matrix.

### 11.2. Gateway tests

- Profile `phase4_c1` fail closed.
- Token issuer/audience/expiry/not-before/scope/sub matrix.
- Đúng một lab user/device.
- Không có write/raw LISP path.
- Migration `0003`, restart và backup/restore.
- Duplicate, stale session, reconnect, cancel intent và package mismatch.
- Capability/package check ngay trước dispatch.
- Entity count lấy từ summary; không suy ra từ mảng entity giả/rỗng.
- Agent error được sanitize trước public boundary.

### 11.3. Shared reconnect/failure matrix

Simulator và Real Agent headless phải bao phủ tối thiểu:

- success;
- delay before ACK/result;
- drop before ACK;
- drop after ACK before start;
- drop after start before result;
- reconnect `not_started`;
- reconnect `started`;
- reconnect terminal succeeded/failed/cancelled;
- duplicate ACK/progress/result;
- out-of-order sequence;
- payload hash mismatch;
- stale heartbeat/session replacement;
- cancel before start/while running/too late;
- package/capability mismatch;
- ledger corruption/contradictory evidence.

Chỉ `not_started` được thực thi lại.

### 11.4. Agent headless tests

- Ledger crash/replay/payload mismatch.
- Terminal persist-before-send.
- WSS reconnect/backoff/manual retry.
- Pause persist qua restart.
- Mapping AutoCAD/File IPC errors.
- Command bị cấm không gọi backend.
- Router không import hoặc nhận full write-capable backend API.
- DPAPI provider được mock ở headless test.

### 11.5. UI tests

Dùng `pytest-qt` offscreen:

- state mapping;
- widget chỉ gửi intent;
- không block UI thread;
- keyboard/focus;
- close-to-tray;
- Exit khi job active/unknown;
- pause/resume persist;
- diagnostics redaction;
- DPI 100%, 150%, 200% và multi-monitor manual checks.

### 11.6. Windows CI/build

- PR gate: Python 3.12 unit/UI, Gateway contract, PowerShell/input validation, lock/static checks và regression matrix.
- Release gate: build standalone folder và upload artifact chạy riêng bằng `workflow_dispatch` trong job `standalone-release`; không chạy build nặng trong PR để tránh timeout.
- Pin/check lockfiles.
- Schema snapshot diff chỉ có delta đã duyệt.
- Smoke run artifact trên Windows runner khi khả thi.

## 12. Thử nghiệm AutoCAD thật

Dùng máy Windows lab và drawing không nhạy cảm:

- Agent start/stop/restart.
- AutoCAD closed.
- No active document.
- Document hợp lệ.
- AutoCAD đang chạy manual command.
- Modal dialog.
- Đổi document trước và trong lúc đọc.
- Dispatcher/package thiếu.
- Package hash hoặc version sai.
- Mất mạng rồi reconnect ở từng điểm failure matrix.
- Gửi trùng cùng command.
- Pause trả `paused_by_user`; resume rồi gọi mới thành công.
- Gateway restart với cùng SQLite.
- Agent restart với cùng local ledger.

Chạy standalone trên máy phát triển và một Windows 11 VM sạch. Ghi startup time, RAM, package size và kết quả Windows Defender/SmartScreen. Phase 4 thu số liệu, chưa đặt production SLO.

## 13. E2E bắt buộc

Thực hiện cùng yêu cầu bằng:

1. MCP protocol client thật.
2. ChatGPT Web sau khi chọn app AutoCAD trong conversation mới.

Prompt nghiệm thu:

> Hãy đọc bản vẽ AutoCAD đang mở và cho biết tên bản vẽ, số entity và danh sách layer.

Kết quả phải:

- đến đúng device allowlist và active document;
- có job ID, command ID, Agent version và package ID/version/SHA-256;
- có revision evidence `summary_only`, `commit_safe=false`;
- không chứa full path, token hoặc drawing content ngoài summary cho phép;
- giữ structured error cho busy, modal, document switch, paused và package mismatch;
- chứng minh máy user không mở inbound listener/tunnel;
- có correlation IDs để truy từ MCP request tới Gateway job, Agent command và package result;
- ghi latency của ít nhất 10 lần gọi nhỏ.

## 14. Điều kiện NO-GO

Không nghiệm thu nếu xảy ra một trong các trường hợp:

- Baseline Phase 0–3.1 regression không xanh.
- Tài liệu/evidence vẫn tuyên bố Phase 3.1 pending dù hosted CI đã là gate bắt buộc.
- Có đường gọi write, generic runtime `call()` hoặc raw LISP từ Agent router.
- Command `started` bị thực thi lại sau reconnect/restart.
- Simulator và Real Agent có reconcile semantics khác nhau.
- Token, credential, full path hoặc drawing content nhạy cảm xuất hiện trong UI/log/DB/diagnostics.
- Gateway route nhầm device hoặc chấp nhận stale/replaced session.
- UI gọi COM/File IPC/WSS trực tiếp hoặc treo khi mất mạng/AutoCAD bận.
- Package mismatch vẫn cho phép chạy.
- `cad_query` trả `total=0` làm sai nghĩa summary có entity.
- Summary revision bị coi là commit-safe.
- Chỉ kiểm metadata/JWKS mà chưa thực hiện AutoCAD read thật bằng token mới.
- Structured error hoặc package provenance không truy được từ kết quả.
- Public cutover chưa có rollback đã thử nghiệm.

## 15. Bằng chứng bàn giao

Artifact nghiệm thu gồm:

- standalone Agent folder;
- Agent artifact SHA-256;
- package manifest và package SHA-256;
- migration/contract snapshot review;
- headless Agent và UI test results;
- shared reconnect/failure matrix results;
- hướng dẫn lab provisioning;
- ảnh UI ready, busy/modal, paused và incompatible;
- diagnostics sample đã redaction;
- Windows packaging metrics;
- correlation IDs của protocol-client E2E và ChatGPT Web E2E;
- xác nhận không có inbound port/tunnel trên máy user;
- public cutover/rollback record;
- tài liệu giới hạn C1 và danh sách phần hoãn Phase 5+.

## 16. Giả định đã khóa

- Phase 4 bắt đầu từ `main@2517ec80e95c88776bcbfb98e0b33078099ef444` hoặc commit mới hơn đã giữ toàn bộ Phase 3.1.
- Public host là `cad.kythuatvang.com`.
- Auth0 hiện tại được tái sử dụng cho lab human OAuth; token mới phải có `autocad.read`.
- Một VPS Gateway worker, một lab user và một lab device.
- SQLite và in-process connection registry tiếp tục là single-writer C1 architecture.
- Python 3.12 x64, PySide6 6.11.1 và standalone folder.
- Credential C1 là lab credential bảo vệ bằng DPAPI; production pairing/device key thuộc Phase 5.
- `cad.agent/1` chỉ giữ nguyên nếu extension additive thật sự backward-compatible.
- C1 observation là summary-only và không commit-safe.
- Không deploy installer, auto-update, Portal, write approval, CAD Program hoặc production multi-user trong Phase 4.
