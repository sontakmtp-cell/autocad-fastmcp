# Phase 2 — FastMCP public facade evidence

Trạng thái: triển khai local hoàn tất; workflow CI matrix đã thêm tại `.github/workflows/phase2-gateway.yml`, chưa được GitHub Actions chạy trong phiên này.

## Phạm vi đã thực hiện

- Package độc lập tại `services/gateway/`, pin chính xác `fastmcp==3.4.4`; lock dependency tách khỏi legacy.
- Composition root dùng `CadApplicationService` của Phase 1 và backend thật theo `AUTOCAD_MCP_BACKEND=auto|file_ipc|ezdxf`.
- `AUTOCAD_MCP_PUBLIC_V1_DXF_PATH` chỉ được đọc từ environment lúc khởi động; MCP không nhận đường dẫn file.
- Local launcher đọc `AUTOCAD_MCP_INTERFACE`, mặc định `legacy`, cho phép `public_v1` và từ chối `dual`.
- No-auth public v1 chỉ bind loopback và có Host/Origin guard trước FastMCP.
- Workflow CI chạy Gateway trên Windows/Linux với Python 3.10/3.12/3.13, kiểm exact FastMCP, test, build wheel và snapshot diff.

## Contract đã khóa

Chỉ expose:

1. `cad_list_devices`
2. `cad_observe`
3. `cad_query`

Resource templates:

- `cad://devices/{device_id}/capabilities`
- `cad://snapshots/{snapshot_id}/summary`
- `cad://snapshots/{snapshot_id}/entities{?cursor,limit,types,layers}`
- `cad://artifacts/{artifact_id}`

Prompt thử nghiệm:

- `plan_cad_change`
- `repair_after_validation`

Mọi tool có `readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`; `cad_observe` không đánh dấu idempotent vì mỗi lần tạo snapshot mới. Schema hash được đóng băng tại `services/gateway/snapshots/tools.json`.

## Luồng dữ liệu và ownership

`cad_observe` đọc drawing/entity/layer/screenshot qua `CadApplicationService`, lọc entity về `entity_id`, `entity_type`, `layer` và geometry allowlist, tạo snapshot immutable trong bộ nhớ và tính `document_revision` bằng SHA-256 JSON chuẩn hóa. `cad_query` chỉ đọc snapshot, lọc tối đa 16 type/layer và phân trang tối đa 100 entity/trang.

Snapshot và artifact lưu `principal.subject`; principal khác chỉ nhận `not_found`, không làm lộ sự tồn tại và không gọi backend cho device không thuộc quyền. Ảnh được lưu như artifact bytes và bị giới hạn bởi `AUTOCAD_MCP_MAX_IMAGE_BYTES` (mặc định 5 MiB).

JWT fixture vẫn dùng scope `autocad.read` để kiểm tra boundary FastMCP; Phase 2 không kết nối Auth0 production và không tạo/ghi `OPENAI_API_KEY`.

## Kiểm thử và bằng chứng

Chạy tại ngày 2026-07-21:

```text
services/gateway: uv run --locked --group test pytest -q
22 passed

services/gateway: uv build --wheel --out-dir <temporary directory>
Successfully built autocad_fastmcp_gateway-0.2.0-py3-none-any.whl

root legacy: uv run pytest tests/ -q
376 passed, 1 skipped, 9 warnings

Phase 0 spike: uv run --locked --group test pytest -q
14 passed

root: git diff --check
clean
```

Gateway tests cover service revision stability, snapshot immutability boundary, geometry allowlist, filters/cursors/limits, ownership isolation, oversized preview, exact tool/resource/prompt snapshots, in-memory flow, Streamable HTTP stateful/stateless, Host/Origin, concurrent correlation IDs, strict input and JWT scope fixture.

## Deferred by design

SQLite, WebSocket, Desktop Agent, multi-user durable ownership, write/preview/commit, `cad_get_job`, `cad_prepare_program`, Auth0 production, ChatGPT Web verification, VPS and deployment remain outside Phase 2.

## Phase 2.1 hardening note

Evidence ở trên là bằng chứng của implementation Phase 2 gốc ngày 2026-07-21;
không bị viết lại. Đợt Phase 2.1 ngày 2026-07-22 giữ nguyên schema/public facade
nhưng thay revision semantics, observation budgets, local snapshot/artifact
lifecycle, PNG verification, Host/Origin policy, correlation-safe errors,
backend-data validation, device state và cursor/filter canonicalization.

Test evidence và các giới hạn mới được ghi riêng tại
`docs/architecture/phase2.1-observation-hardening-evidence.md`.
