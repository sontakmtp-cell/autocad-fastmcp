# Phase 4 C1 implementation evidence

> Cập nhật: 2026-07-23
> Quyết định hiện tại: **NO-GO cho nghiệm thu Phase 4 đầy đủ**
> Lý do: implementation, local automation, standalone local, AutoCAD failure matrix thật và operator deployment đã có; vẫn chưa hoàn tất Windows 11 VM sạch, hosted standalone artifact, token/protocol-client evidence và rollback cutover.

## 1. Phạm vi đã triển khai

- Shared protocol `cad.agent/1` được mở rộng additive với Agent/runtime/document/pause/package evidence; simulator Phase 3 vẫn dùng hello cũ.
- Gateway profile opt-in `phase4_c1`, public contract `cad.mcp/1.2`, một lab user/device, write luôn tắt, OAuth JWT fail-closed và Host/Origin guard cho cả `/mcp` lẫn `/agent/ws`.
- Migration ordered `0003_phase4_c1.sql` giữ Agent/package/runtime và revision evidence.
- Observation C1 chỉ nhận summary đã kiểm chặt: basename bản vẽ, entity count, tối đa 256 layer, entity detail rỗng, `summary_only`, `commit_safe=false` và package khớp tuyệt đối. `cad_query` trả `capability_missing`.
- Desktop Agent Windows có outbound WSS, HMAC hello proof, durable SQLite ledger, replay/reconcile, terminal persist-before-send, hard pause persist, manual retry, diagnostics allowlist và read-only router.
- PySide6 Widgets UI bằng tiếng Việt theo phụ lục: server, AutoCAD, document, task, version/package, support code, retry, hard pause, diagnostics, help, close-to-tray và confirm khi thoát lúc có job.
- Package AutoLISP `autocad.lisp.drawing_info@3.3-c1` chỉ đọc summary và có SHA-256. Provision lưu lab credential bằng Windows DPAPI; script không sửa AutoCAD profile.
- Build standalone dùng Python 3.12, PySide6 6.11.1 và Nuitka 2.8.9. Workflow Windows có job test/validate cho PR; job build/upload standalone nặng `standalone-release` chỉ chạy bằng `workflow_dispatch` và có cache Nuitka, transcript log và manifest validation.

Human OAuth metadata/challenge được cấu hình theo yêu cầu MCP authorization hiện hành về protected-resource metadata, authorization server discovery, PKCE và resource binding: [OpenAI Apps SDK authentication](https://developers.openai.com/apps-sdk/build/auth#mcp-authorization-spec-requirements).

## 2. Bằng chứng local

| Gate | Kết quả | Bằng chứng |
| --- | --- | --- |
| Root regression | PASS | `380 passed, 1 skipped`; 9 cảnh báo pyparsing có sẵn |
| Gateway | PASS | `176 passed` |
| Desktop Agent + UI | PASS | `23 passed` trên Windows, gồm DPAPI thật, WSS reconnect thật, restart/reconcile ledger, chuẩn hóa mã dispatcher và đóng SQLite ledger khi thoát bình thường |
| CAD Core | PASS | `1 passed` |
| UI render | PASS local | Windows platform render đúng tiếng Việt và bố cục C1; offscreen backend không có font tiếng Việt nhưng pytest-qt vẫn kiểm state/intent/tray |
| Standalone build | PASS local | `cad.agent.release/1`; 320 files; khoảng 267.6 MiB trước tối ưu; Agent SHA-256 `0ac85e5d779f75d06e82288702aa0c7a96b7bb97e05b8642f0dcbf50773df951`; Agent/package hash khớp manifest; UI smoke PASS |
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

### 2.1. Hosted CI sau khi sửa workflow

- Commit tham chiếu: `6a6022b` (`Run standalone exe build only on manual dispatch`).
- PR checks của nhánh đã PASS, gồm `windows-agent-tests`, validate input standalone, regression Phase 0–3.1, wheel/lock/static checks và các matrix Python 3.10/3.12/3.13 trên Ubuntu/Windows. Run tham chiếu: [Phase 4 C1 Agent #29975105571](https://github.com/sontakmtp-cell/autocad-fastmcp/actions/runs/29975105571).
- Job `standalone-release` hiện `skipping` trên PR theo thiết kế; job này chỉ chạy khi gọi `workflow_dispatch`. Vì vậy PR xanh không đồng nghĩa đã có artifact standalone hosted.
- Hai commit CI liên quan: `5ffed6f` tách build nặng khỏi PR check; `6a6022b` giới hạn job release vào manual dispatch. Lần chạy `workflow_dispatch` sau cùng để xác nhận artifact/log hosted vẫn là gate còn thiếu.

Operator đã báo cáo và kiểm tra việc thiết lập VPS Gateway, Cloudflare và kết nối ChatGPT. Evidence public metadata/MCP read, token scope read mới và MCP protocol-client chưa được gắn vào hồ sơ này, nên vẫn tách riêng operator report khỏi protocol evidence.

Build local ghi nhận Windows Defender giữ executable ngắn hạn trong post-processing; Nuitka retry và hoàn tất. Đây là số đo lab, chưa phải xác nhận SmartScreen/Defender trên Windows 11 VM sạch.

### 2.2. Trace AutoCAD thật

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

## 3. Gate chưa có evidence

- AutoCAD failure matrix thật và protocol reconnect/restart matrix local đã hoàn tất; chưa có bằng chứng mất mạng Internet/VPS thật.
- Chưa provision credential lab thật hoặc revoke/reconnect credential trên Gateway public.
- Operator đã thiết lập một Gateway worker, SQLite persistent volume, Cloudflare Tunnel và kết nối ChatGPT; chưa có public metadata/MCP read evidence đính kèm trong hồ sơ này.
- Chưa chạy Auth0 issuer/audience/scope/sub matrix bằng token thật mới cấp.
- Chưa lưu bằng chứng cùng prompt nghiệm thu bằng MCP protocol client và ChatGPT Web; kết nối ChatGPT hiện mới được phân loại là operator report.
- Chưa chạy `workflow_dispatch` để lưu artifact/log standalone hosted sau khi tách job release khỏi PR.
- Đã có 10 mẫu latency local qua AutoCAD thật; chưa có Windows 11 VM sạch, RAM/startup, Defender/SmartScreen và rollback cutover.

Không được đổi Phase 4 sang `GO` cho tới khi toàn bộ mục trên có artifact/timestamp/correlation IDs và không rò token, full path hoặc drawing content ngoài summary.

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
