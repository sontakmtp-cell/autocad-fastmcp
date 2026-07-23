# Phase 4 C1 implementation evidence

> Cập nhật: 2026-07-23
> Quyết định hiện tại: **GO — Phase 4 C1 đã hoàn tất và được nghiệm thu**
> Lý do: implementation, standalone thật, public ChatGPT/protocol-client E2E, Windows 11 VM sạch, operator SmartScreen observation và public rollback/revoke có kiểm soát đều đã đạt. Production đã được phục hồi và Phase 5 chưa được triển khai.

## 1. Phạm vi đã triển khai

- Shared protocol `cad.agent/1` được mở rộng additive với Agent/runtime/document/pause/package evidence; simulator Phase 3 vẫn dùng hello cũ.
- Gateway profile opt-in `phase4_c1`, public contract `cad.mcp/1.2`, một lab user/device, write luôn tắt, OAuth JWT fail-closed và Host/Origin guard cho cả `/mcp` lẫn `/agent/ws`.
- Migration ordered `0003_phase4_c1.sql` giữ Agent/package/runtime và revision evidence.
- Observation C1 chỉ nhận summary đã kiểm chặt: basename bản vẽ, entity count, tối đa 256 layer, entity detail rỗng, `summary_only`, `commit_safe=false` và package khớp tuyệt đối. `cad_query` trả `capability_missing`.
- Desktop Agent Windows có outbound WSS, HMAC hello proof, durable SQLite ledger, replay/reconcile, terminal persist-before-send, hard pause persist, manual retry, diagnostics allowlist và read-only router.
- PySide6 Widgets UI bằng tiếng Việt theo phụ lục: server, AutoCAD, document, task, version/package, support code, retry, hard pause, diagnostics, help, close-to-tray và confirm khi thoát lúc có job.
- Package AutoLISP `autocad.lisp.drawing_info@3.3-c1` chỉ đọc summary và có SHA-256. Provision lưu lab credential bằng Windows DPAPI; script không sửa AutoCAD profile.
- Build standalone dùng Python 3.12, PySide6 6.11.1 và Nuitka 2.8.9. Bản phát hành bắt buộc include toàn bộ package `websockets`, có `--package-self-test` để phát hiện thiếu dynamic import, tự chọn MSVC/MinGW, tính SHA-256 bằng .NET để chạy ổn trên Windows PowerShell 5.1 và có diagnostics stage/type không lộ secret.
- GitHub Actions chỉ chạy test/validate build inputs. Standalone `.exe` được build, hash, quét Defender và chạy E2E trên máy Windows phù hợp; không build executable bằng GitHub hosted runner.

Human OAuth metadata/challenge được cấu hình theo yêu cầu MCP authorization hiện hành về protected-resource metadata, authorization server discovery, PKCE và resource binding: [OpenAI Apps SDK authentication](https://developers.openai.com/apps-sdk/build/auth#mcp-authorization-spec-requirements).

## 2. Bằng chứng local

| Gate | Kết quả | Bằng chứng |
| --- | --- | --- |
| Root regression | PASS | `381 passed, 1 skipped`; 9 cảnh báo pyparsing có sẵn |
| Gateway | PASS | `176 passed` |
| Desktop Agent + UI | PASS | `32 passed` trên Windows, gồm DPAPI thật, WSS reconnect thật, restart/reconcile ledger, diagnostics lỗi kết nối an toàn, chuẩn hóa mã dispatcher và đóng SQLite ledger khi thoát bình thường |
| CAD Core | PASS | `1 passed` |
| UI render | PASS local | Windows platform render đúng tiếng Việt và bố cục C1; offscreen backend không có font tiếng Việt nhưng pytest-qt vẫn kiểm state/intent/tray |
| Standalone build | PASS local | `cad.agent.release/1`; 309 app files; 255.78 MiB; executable 136,027,648 byte; Agent SHA-256 `6bf5147acf523a0170512318b1280e9e2d4a7dfa76f2081842fea7ab7960e782`; package SHA-256 khớp manifest; `--package-self-test` exit 0 trong 207.77 ms |
| Standalone runtime | PASS local + public | Executable standalone mở outbound WSS IPv6:443, UI `Sẵn sàng`, AutoCAD/document/package đúng; ChatGPT Web và protocol client đều đọc AutoCAD thật qua chính executable này |
| Windows package metrics | PASS local + VM sạch + SmartScreen | Máy lab: working set 136.82 MiB, private 62.48 MiB. VM sạch: cold/warm self-test 23,673.05/340.99 ms, peak warm working set/private 61.62/28.79 MiB; Defender exact-file scan 0 threat; Authenticode `NotSigned`; SmartScreen chặn bản có Mark-of-the-Web đúng thiết kế |
| Static/package checks | PASS | Python compile, PowerShell parse, wheel builds, `git diff --check` |
| AutoCAD thật happy path | PASS local | AutoCAD Mechanical 2025, `drawing33.dwg`, 30 entity, layers `0`, `DIM`, `CENTER`; Gateway → WSS → Agent → File IPC → AutoLISP `3.3-c1` |
| Hard pause/resume | PASS local | Lệnh mới bị chặn bằng `paused_by_user`; resume rồi đọc lại thành công |
| Không có bản vẽ mở | PASS local | Giữ AutoCAD ở màn hình Start; toàn tuyến trả `no_active_document`, job `failed`, không tự tạo bản vẽ |
| AutoCAD đang bận | PASS local | Giữ lệnh `LINE` chờ điểm đầu tiên; toàn tuyến trả `autocad_busy`, job `failed`, không chen lệnh vào thao tác người dùng |
| Hộp thoại modal | PASS local | Mở `OPTIONS`; lần đầu phát hiện mã quá rộng `autocad_busy`, sau khi sửa nhận diện cửa sổ Windows thì toàn tuyến trả đúng `modal_dialog_active` và không tác động hộp thoại |
| Đổi active document | PASS local | Chuyển từ `drawing33.dwg` sang `Drawing1.dwg` ngay khi kết quả IPC xuất hiện; sau khi bổ sung re-check và chờ trạng thái chuyển tiếp, toàn tuyến trả `active_document_changed`, loại bỏ kết quả cũ và phục hồi tab ban đầu |
| Dispatcher/package thiếu hoặc sai | PASS local | Tạm vô hiệu hóa dispatcher trên `Drawing1.dwg` trả `dispatcher_not_loaded`; đổi runtime version trả `package_mismatch`; cả hai lần package được nạp lại và tab ban đầu được phục hồi |
| AutoCAD đã tắt | PASS local | Không có `acad.exe`; sau khi sửa bộ dò để bỏ qua cửa sổ File Explorer trùng chữ AutoCAD, toàn tuyến trả `autocad_not_running` và không tự khởi động AutoCAD |
| AutoCAD đóng rồi mở lại | PASS local | Giữ nguyên Agent/Gateway session từ lúc `autocad_not_running`; sau khi mở lại `drawing33.dwg`, cùng session đọc thành công 30 entity, `summary_only`, không restart Agent |
| Network/restart matrix | PASS local automation | Agent WSS reconnect sau ACK báo `started` và executor chỉ chạy 1 lần; restart ledger trả `not_started/started/terminal`; Gateway restart/hardening 15 PASS; shared real-WSS reconnect matrix 7 PASS |
| Latency AutoCAD thật | PASS local | 10/10 kết quả ổn định; min 282.46 ms, median 291.98 ms, p95 299.35 ms, max 301.55 ms |

### 2.1. Chính sách GitHub Actions sau khi kiểm tra hosted build

- Commit implementation tham chiếu: `8a18731` (`fix: complete Phase 4 standalone runtime checks`).
- PR checks của nhánh đã PASS, gồm `windows-agent-tests`, validate input standalone, regression Phase 0–3.1, wheel/lock/static checks và các matrix Python 3.10/3.12/3.13 trên Ubuntu/Windows. Run tham chiếu: [Phase 4 C1 Agent #29975105571](https://github.com/sontakmtp-cell/autocad-fastmcp/actions/runs/29975105571).
- Run [#30003442857](https://github.com/sontakmtp-cell/autocad-fastmcp/actions/runs/30003442857) xác nhận `windows-agent-tests` PASS và MinGW bắt đầu build, nhưng timeout đúng 35 phút ở bước Nuitka; không có lỗi compiler.
- Run [#30005983743](https://github.com/sontakmtp-cell/autocad-fastmcp/actions/runs/30005983743) đã bị hủy theo quyết định operator. Job `standalone-release` được gỡ hẳn khỏi workflow.
- Chính sách khóa: GitHub Actions chỉ chạy unit/integration/contract/static và build-input checks. Artifact phát hành Phase 4 chỉ được tạo bằng `scripts/build-phase4-agent.ps1` trên máy Windows phù hợp; local manifest/hash/package self-test/public E2E là bằng chứng executable có thẩm quyền.

### 2.2. Public OAuth, ChatGPT Web và protocol client

- `https://cad.kythuatvang.com/healthz` trả 200 `ok`; `/readyz` trả 200 `ready`.
- `/.well-known/oauth-protected-resource/mcp` trả resource `https://cad.kythuatvang.com/mcp`, scope `autocad.read` và Auth0 issuer. Auth0 discovery công bố Dynamic Client Registration và PKCE `S256`.
- POST `/mcp` không token trả 401 cùng `WWW-Authenticate` trỏ về protected-resource metadata; Gateway không fail-open.
- ChatGPT Web đã kết nối app `Kỹ Thuật Vàng AutoCAD`, xin scope `autocad.read` và load đúng 4 tool: `cad_list_devices`, `cad_observe`, `cad_query`, `cad_get_job`.
- Cùng prompt nghiệm thu chạy lại bằng standalone Agent trong [conversation ChatGPT](https://chatgpt.com/c/6a61fc28-38b4-83ec-8671-994472aa4bd8): `drawing33.dwg`, 30 entity, layers `0`, `DIM`, `CENTER`. Trace cuối: job `job-389ec9b9-90ea-4143-a83e-650250fa1935`, command `command-c51ba20a-8ab2-4c13-ab3b-63152b3600f8`, session `session-ea75a6a8-bf7d-4d21-b78a-9685bcf790cc`.
- Protocol client độc lập `scripts/phase4_public_mcp_e2e.py` dùng DCR + PKCE, chỉ giữ client/token trong memory và chạy `initialize -> tools/list -> cad_list_devices -> cad_observe -> cad_get_job`. Kết quả PASS: job `job-80d01d8d-68a7-477e-a983-dbbd033ebd9d`, command `command-41166ded-52cd-4dcc-9d31-00c6cc7e2936`, snapshot `snapshot-command-41166ded-52cd-4dcc-9d31-00c6cc7e2936`, cùng session và cùng drawing summary nêu trên.
- Máy lab chỉ mở outbound WSS tới Cloudflare/VPS trên 443; không mở tunnel/inbound listener cho Gateway.

### 2.3. Trace AutoCAD thật

- Correlation ID: `corr-real-autocad-clean`.
- Job ID: `job-f69e30cc-a53d-422a-af77-c91869e8ce72`.
- Command ID: `command-d97f60eb-d9a9-4d8c-ae9d-5af52d8d2dc6`.
- Agent `0.1.0`; package SHA-256 `203911d56a258d9b422ba5fb29002372ffc3835439be99c19486e04915373736`.
- Output dùng `cad.mcp/1.2`, `summary_only`, `commit_safe=false`; không chứa full path.
- Failure trace không có bản vẽ: correlation `corr-real-no-active-document`, job `job-ef540e83-a38a-4adb-b696-4f2440540802`, lỗi `no_active_document`, state `failed`.
- Failure trace AutoCAD đang bận: correlation `corr-real-autocad-busy`, job `job-730fa176-cf6f-43aa-b0e1-2263079bc7d9`, lỗi `autocad_busy`, state `failed`.
- Failure trace hộp thoại modal sau sửa lỗi: correlation `corr-real-modal-dialog-fixed`, job `job-daf1fb33-2d6e-49e5-9bcb-60eaffa9bbd6`, lỗi `modal_dialog_active`, state `failed`.
- Failure trace đổi active document sau sửa lỗi: correlation `corr-real-active-document-changed-final`, job `job-e4b08f1e-3739-49aa-8b7b-93f8dafe12f1`, lỗi `active_document_changed`, state `failed`; tab ban đầu đã được phục hồi.
- Failure trace dispatcher thiếu: correlation `corr-real-dispatcher-not-loaded`, job `job-6d4ac24d-d628-4981-ab71-c49fe54dd14b`, lỗi `dispatcher_not_loaded`, state `failed`; package đã được nạp lại.
- Failure trace dispatcher sai version: correlation `corr-real-package-mismatch`, job `job-bfcc4d5e-7a1d-4896-a0ed-1a45dede4a80`, lỗi `package_mismatch`, state `failed`; package đã được nạp lại.
- Failure trace AutoCAD đã tắt sau sửa lỗi: correlation `corr-real-autocad-not-running-fixed`, job `job-a20a5dfa-67ef-4207-bfd9-94c1293a18b8`, lỗi `autocad_not_running`, state `failed`; AutoCAD không bị tự khởi động.
- Reconnect AutoCAD: giữ session `session-fe415bcc-1399-4449-8710-bb4f999abf96`; correlation `corr-reconnect-autocad-reopened`, job `job-67d930c4-1aa9-4aff-9cca-30040333d874`, command `command-58372204-7f37-470d-9c3d-ba315f44dd49`; 30 entity, `summary_only`.

### 2.4. Windows 11 VM sạch

- VM `Phase4-Win11-Clean` được tạo mới trên Hyper-V Generation 2: Secure Boot `MicrosoftWindows`, TPM ảo bật, 4 vCPU, RAM động 4-12 GiB và VHDX động 80 GiB.
- Hệ điều hành là Microsoft Windows 11 Enterprise Evaluation 25H2 x64 EN-US, build `26200`, cài mới ngày 2026-07-23. ISO chính thức 7,092,807,680 byte có SHA-256 `a61adeab895ef5a4db436e0a7011c92a2ff17bb0357f58b13bbc4062e535e7b9`, khớp tài liệu hash Microsoft.
- Trước khi chuyển artifact vào VM: `AgentRootExists=false`, `ConfigExists=false`, `CredentialExists=false`, `ArtifactExists=false`. Không dùng lại config hoặc DPAPI blob từ máy lab.
- Artifact được chuyển sau phép đo sạch. Executable trong VM có 136,027,648 byte và SHA-256 `6bf5147acf523a0170512318b1280e9e2d4a7dfa76f2081842fea7ab7960e782`, khớp `manifest.json`.
- Cold `--package-self-test` lần đầu exit 0 trong 23,673.05 ms; lần warm exit 0 trong 340.99 ms. Peak warm working set 61.62 MiB, peak private memory 28.79 MiB.
- Microsoft Defender trong VM bật antivirus và real-time protection. Exact-file custom scan exit 0, báo `found no threats`, không có threat detection mới.
- Authenticode là `NotSigned`. Operator đăng nhập VM và mở shortcut trỏ tới đúng executable có SHA-256 trên, kèm Mark-of-the-Web `ZoneId=3`. Microsoft Defender SmartScreen hiển thị `Windows protected your PC` và chặn `unrecognized app from starting`; operator xác nhận nội dung, sau đó chọn `Don't run`. Không bấm `More info`, không `Run anyway` và không bypass bảo mật.

### 2.5. Cửa sổ bảo trì public rollback/revoke

- Trước thay đổi: `/healthz` 200, `/readyz` 200, POST `/mcp` không token 401 đúng challenge. Backup nhất quán của environment, systemd units và SQLite được tạo tại `/var/backups/autocad-mcp/phase4-maintenance-20260723T194528`; file secret không được in ra log.
- Public cutover rollback: dừng đúng `autocad-mcp-cloudflared.service` làm public trả Cloudflare 530 sau 2.8 giây và giữ 530 trong toàn bộ 6 mẫu. Bật lại service làm `/healthz` trở về 200 sau 2.6 giây; cả Gateway và tunnel đều `active`.
- Revoke lab credential: Agent production C1 tạo session `session-b77a357b-ef63-4373-88ff-16b293cdcdbb`; thay credential allowlist phía Gateway bằng giá trị vô hiệu và restart Gateway làm session này đóng lúc `2026-07-23T12:56:15.531898+00:00`. Agent không có TLS connection tới Gateway khi credential không khớp.
- Restore dùng chính file environment đã backup và restart Gateway/tunnel. `cmp` xác nhận environment khớp byte-for-byte; public đi qua 530/502 rồi `/healthz` trở về 200 sau 4.6 giây. Agent tự kết nối lại sau 8.6 giây thành session `session-5c7ef652-4bcf-4564-8cd7-ad04a9316742`.
- Một lệnh đo phía máy operator bị timeout trước khi khối `finally` hoàn tất. Audit ngay sau timeout phát hiện environment chưa restore và tunnel đang `deactivating`; recovery command riêng đã restore file, start cả hai service và xác minh `active`. Đây là bằng chứng recovery thực tế, đồng thời runbook không được dựa vào client-side `finally` khi tool có thể bị hard-timeout.
- Sau phục hồi: `/healthz` 200, `/readyz` 200, protected-resource metadata 200, POST `/mcp` không token 401; Agent session mới tiếp tục heartbeat. Test chạy lại trong cùng cửa sổ: Gateway `176 passed`, Desktop Agent `32 passed`.

## 3. Giới hạn evidence còn lại — không chặn Phase 4

- AutoCAD failure matrix thật và protocol reconnect/restart matrix local đã hoàn tất. Public tunnel outage, Gateway restart và recovery đã có bằng chứng; chưa làm bài mất toàn bộ VPS host vì cutover rollback đã chứng minh cùng public failure/recovery boundary cần cho C1.
- Credential lab thật đã provision bằng DPAPI; revoke allowlist đã đóng session hiện tại, reject reconnect và restore tạo session mới.
- Public metadata, 401 challenge, Auth0 discovery, token scope read, ChatGPT Web và protocol-client đã có evidence; invalid issuer/audience/sub vẫn dựa trên automated test matrix, chưa phát hành nhiều token thật sai claim.
- Windows 11 VM sạch, startup/RAM/hash, Defender exact-file scan và operator SmartScreen observation đã có evidence. Bản có Mark-of-the-Web bị chặn và được đóng bằng `Don't run`; không bypass UI bảo mật.
- Public cutover rollback/revoke đã thực hiện trong cửa sổ bảo trì và production đã được phục hồi.

Review cuối xác nhận không in token/device credential, không đưa drawing content ngoài summary vào evidence, production trở lại 200/401 với Agent session mới và GitHub không build/upload `.exe`. Các giới hạn nêu trên được chấp nhận cho C1 read-only; Phase 4 là `GO`.

## 4. Lệnh tái lập

```powershell
# Unit/UI Agent
Set-Location apps/desktop_agent
uv sync --locked --python 3.12 --group build --group test --group ui-test
uv run --no-sync pytest -q

# Gateway
Set-Location ../../services/gateway
uv sync --locked --group test
uv run --no-sync pytest -q

# Standalone artifact
Set-Location ../..
.\scripts\build-phase4-agent.ps1
```

Provision và chạy máy lab được mô tả tại [Desktop Agent README](../../apps/desktop_agent/README.md).
