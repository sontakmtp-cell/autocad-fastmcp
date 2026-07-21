# Kế hoạch chi tiết Phase 0 — FastMCP facade compatibility spike

## 1. Mục tiêu và điều kiện hoàn thành

Phase 0 chỉ chứng minh FastMCP có phù hợp để làm lớp giao tiếp ChatGPT của Gateway hay không. Không xây Gateway thật, WebSocket, SQLite, Desktop Agent, CAD Program hoặc write AutoCAD.

Hiện tại:

- Legacy server dùng FastMCP đi kèm `mcp==1.26.0`.
- Baseline vừa kiểm tra: `302 passed, 1 skipped, 9 warnings`.
- `fastmcp==3.4.4` yêu cầu Python `>=3.10`; Phase 0 kiểm Python 3.10, 3.12 và 3.13 trên Windows/Linux. [FastMCP 3.4.4 trên PyPI](https://pypi.org/project/fastmcp/3.4.4/)
- FastMCP mới kéo Starlette `>=1.0.1`, trong khi legacy đang khóa Starlette `0.52.1`; vì vậy POC phải có môi trường và lockfile riêng.

Phase 0 chỉ đạt `GO` khi:

- Legacy entrypoint, `pyproject.toml`, `uv.lock` và `src/autocad_mcp` không thay đổi.
- FastMCP facade chỉ expose đúng ba tool thử nghiệm.
- In-memory và Streamable HTTP stateful/stateless đều chạy `initialize → tools/list → tools/call`.
- Structured output, schema, annotations, `ImageContent`, `ResourceLink`, auth claims, host/origin guard và lỗi an toàn đều được chứng minh.
- Toàn bộ ma trận Windows/Linux và Python yêu cầu vượt qua.
- Có ADR, schema snapshots, dependency report và evidence report để Khầy duyệt trước Phase 1.

## 2. Các hạng mục triển khai

### 2.1. Khóa baseline

- Ghi commit hiện tại, `git status`, phiên bản Python/uv/MCP và hash của root `pyproject.toml` cùng `uv.lock`.
- Giữ nguyên `docs/phase0-baseline.md` vì đó là Phase 0 cũ của HTTP bridge; tài liệu mới phải mang tên riêng để tránh nhầm.
- Chạy và lưu kết quả:
  - `uv sync --locked`
  - `uv run pytest tests/ -q`
  - `git diff --check`
- Ghi nhận các file dirty/untracked hiện hữu và tuyệt đối không reset, xóa hoặc ghi đè chúng.

### 2.2. Tạo POC độc lập

Tạo project riêng tại `poc/fastmcp-phase0/` gồm source, test, snapshots, `pyproject.toml` và `uv.lock` riêng.

Quyết định dependency:

- Pin chính xác `fastmcp==3.4.4`; không dùng khoảng phiên bản.
- Python POC: `>=3.10,<3.14`; Python 3.12 là runtime tham chiếu.
- Dùng root project làm path dependency chỉ để tái sử dụng `CommandResult`, `AutoCADBackend` và `EzdxfBackend`.
- Test dependencies nằm trong dependency group riêng.
- Không thêm FastMCP standalone vào root project.

FastMCP khuyến nghị pin chính xác vì minor release vẫn có thể chứa thay đổi giao thức; cấu hình transport của v3 cũng được truyền cho `http_app()`/`run()` thay vì constructor như bản SDK đang dùng. [Hướng dẫn cài đặt](https://gofastmcp.com/getting-started/installation), [hướng dẫn nâng từ MCP SDK](https://gofastmcp.com/getting-started/upgrading/from-mcp-sdk)

### 2.3. Tách contract, service và FastMCP adapter

Bên trong POC chia ba lớp:

```text
Pydantic contracts
    ↓
Fake/Ezdxf application services
    ↓
FastMCP handlers + result mapper
```

Quy tắc:

- Contract và service không import FastMCP.
- `Context`, token, content block và decorator chỉ xuất hiện trong lớp MCP adapter.
- Không dùng `_tool_manager`, private attribute hoặc private module.
- Correlation ID được tạo ở MCP boundary rồi truyền xuống service; test dùng ID factory cố định.
- `CommandResult(ok=False)` được chuyển thành lỗi MCP an toàn, không trả nhầm như kết quả thành công.
- Fake store chỉ nằm trong RAM và được dựng mới cho từng test.

### 2.4. Dựng FastMCP server và outer ASGI

Tạo hai factory:

- `build_mcp_server(services, auth, stateless_http)` đăng ký component.
- `create_app(...)` tạo outer Starlette app.

Outer app:

- MCP endpoint chính xác tại `/mcp`.
- Có `/healthz` chỉ trả trạng thái process.
- Truyền lifespan của FastMCP app cho outer Starlette; thiếu bước này có thể làm session manager không khởi tạo. [FastMCP HTTP deployment](https://gofastmcp.com/v2/deployment/http)
- Mặc định POC và ADR chọn `stateless_http=True` cho Gateway tương lai vì trạng thái bền sẽ nằm ở domain/DB, không nằm trong MCP session.
- Vẫn chạy test stateful để bảo đảm tương thích client.
- Cho phép request không có `Origin`; nếu có thì Origin phải nằm trong allowlist.
- Host sai hoặc Origin không được phép trả `403` trước khi tool chạy.
- Không tin `X-Forwarded-Host` trong POC.
- Chưa thêm `/agent/ws`, `/readyz`, DB lifespan hoặc reverse-proxy production config.

### 2.5. Authentication spike

- Dùng `JWTVerifier` và `RemoteAuthProvider` của FastMCP bằng RSA key/token sinh trong test; không phụ thuộc Auth0 thật hoặc ChatGPT Web.
- Token hợp lệ phải có:
  - `sub`
  - audience đúng
  - issuer đúng
  - chưa hết hạn
  - scope `autocad.read`
- Tạo `AuthenticatedPrincipal` từ `sub`; không dùng `client_id`/`azp` làm user.
- Cả ba tool yêu cầu `autocad.read`.
- Test xác nhận handler/service đọc được `sub` và scopes qua public auth dependency. FastMCP cung cấp `get_access_token()` cho claims và scope ở HTTP transport. [FastMCP authorization](https://gofastmcp.com/servers/authorization)
- Kiểm protected-resource metadata của `RemoteAuthProvider`.
- Không thực hiện Auth0 DCR hoặc login thật trong Phase 0.

### 2.6. Snapshot, ADR và báo cáo

Tạo:

- `docs/architecture/adr/0001-fastmcp-3-gateway-facade.md`
- `docs/architecture/phase0-fastmcp-compatibility-evidence.md`
- Workflow riêng `.github/workflows/phase0-fastmcp.yml`

ADR chốt:

- FastMCP chỉ thuộc MCP interface của Gateway.
- Pin `fastmcp==3.4.4`.
- Outer Starlette sở hữu routing/lifecycle.
- `/mcp` dùng stateless mặc định.
- Domain, Agent transport và persistence không phụ thuộc FastMCP.
- Phase 1 chỉ được mở sau khi Phase 0 đạt `GO` và evidence được duyệt.

Evidence report phải chứa baseline, dependency tree, schema diff, ma trận test, giới hạn chưa kiểm chứng và kết luận `GO/NO-GO`.

## 3. Public interface của POC

Không thay public interface production. POC chỉ expose:

| Tool | Input phẳng | Output chính | Annotation |
|---|---|---|---|
| `cad_list_devices` | `online_only=false`, `capability=null` | `contract_version`, `correlation_id`, danh sách device, default device | read-only, idempotent, non-destructive, closed-world |
| `cad_observe` | `device_id`, `observation_level=summary`, `include_preview_image=false` | snapshot, document revision, summary URI, artifact refs | read-only, non-idempotent, non-destructive, closed-world |
| `cad_get_job` | `job_id`, `event_cursor=null` | state, progress, result/error, next cursor | read-only, idempotent, non-destructive, closed-world |

Contract mặc định là `cad.mcp/0.1`.

Fake data cố định:

- Một device online hỗ trợ `observe` và `screenshot`.
- Một device offline hỗ trợ `observe`.
- Một completed job và một running job.
- `cad_observe` đọc một DXF fixture trong RAM bằng `EzdxfBackend`; không đọc hay ghi DWG thật.

Resources:

- `cad://snapshots/{snapshot_id}/summary`
- `cad://artifacts/{artifact_id}`

`cad_observe` trả structured content và `ResourceLink`; khi `include_preview_image=true`, bổ sung một PNG `ImageContent` có giới hạn kích thước. Structured output được khai báo bằng Pydantic/output schema và map rõ vào `ToolResult`. FastMCP hỗ trợ structured content song song với content blocks. [FastMCP tools và structured output](https://gofastmcp.com/servers/tools)

Lỗi:

- Input sai kiểu/giới hạn: MCP validation error.
- Device, job, snapshot hoặc artifact không tồn tại: `isError=true` với mã `not_found`.
- Backend trả lỗi: mã `backend_error`.
- Exception ngoài dự kiến: `internal_error`, không lộ traceback, path hoặc token.
- Mọi trường hợp lỗi đều phải chứng minh service không bị gọi ngoài ý muốn.

## 4. Kế hoạch kiểm thử

### Contract và component

- `tools/list` chỉ có đúng ba tool, đúng input/output schema và annotations.
- Snapshot JSON được chuẩn hóa theo tên tool và key; test chỉ so sánh, không tự ghi đè.
- Thay đổi snapshot chỉ qua lệnh cập nhật rõ ràng và phải xuất diff để review.
- `resources/list/read` đọc được summary và PNG artifact.
- `cad_observe` có cả structured content, `ResourceLink` và optional `ImageContent`.
- Không có import/private attribute FastMCP bị cấm.

### Transport và ASGI

Chạy bằng cả FastMCP Client và MCP SDK `ClientSession` để tránh kết quả xanh giả do client/server cùng framework:

- In-memory: initialize, list, call cả ba tool, read resource.
- HTTP stateful: initialize, list, call, session cleanup.
- HTTP stateless: initialize, list, call lặp lại.
- `/mcp` đúng path và FastMCP lifespan thực sự được chạy.
- Hai request đồng thời không chia sẻ principal/correlation ID.
- Host hợp lệ, Host sai, Origin hợp lệ, Origin vắng mặt và Origin sai.

FastMCP khuyến nghị in-memory client cho contract test nhưng network transport vẫn cần kiểm riêng. [FastMCP testing](https://gofastmcp.com/servers/testing)

### Auth và error

- Token hợp lệ với `sub` và `autocad.read`.
- Thiếu scope không nhìn thấy/gọi được tool và service spy không nhận call.
- Sai signature, issuer, audience hoặc expiry trả `401`.
- Không có token trả `401`.
- Protected-resource metadata đúng base URL và authorization server.
- Token nguyên bản không xuất hiện trong log hoặc output.
- Lỗi domain và lỗi bất ngờ đều được mask đúng.

### Ma trận CI

Workflow Phase 0 chạy độc lập với workflow legacy:

- OS: `ubuntu-latest`, `windows-latest`
- Python: `3.10`, `3.12`, `3.13`
- Mỗi job:
  - sync POC bằng lockfile riêng;
  - xác nhận FastMCP đúng `3.4.4`;
  - chạy toàn bộ POC tests;
  - kiểm snapshots không đổi.

Sau cùng chạy lại root suite và xác nhận:

```powershell
uv run pytest tests/ -q
git diff --check
git diff --exit-code -- pyproject.toml uv.lock src/autocad_mcp
```

## 5. Gate, phạm vi loại trừ và rollback

`GO` khi toàn bộ test bắt buộc vượt qua trên sáu tổ hợp OS/Python, root suite không giảm, không có private API và schema/evidence đầy đủ.

`NO-GO` nếu xảy ra một trong các trường hợp:

- FastMCP 3.4.4 không chạy trên một môi trường bắt buộc.
- Outer ASGI/lifespan không ổn định.
- Không truy cập được `sub`/scope bằng public API.
- Structured output hoặc content blocks sai giao thức.
- Cần sửa legacy entrypoint hay root dependency để POC hoạt động.
- Host/origin hoặc invalid-token request vẫn tới được service.

Warning cũ của `ezdxf/pyparsing` được ghi nhận nhưng không chặn; warning mới từ POC phải được phân loại trong evidence.

Không thuộc Phase 0:

- Auth0/ChatGPT login thật.
- Multi-user ownership thật.
- SQLite, WebSocket, Agent, AutoCAD thật.
- Tool write, AutoLISP, CAD Program, approval hay rollback.
- Deploy VPS hoặc thay launcher hiện tại.

Rollback chỉ cần xóa/revert project POC, workflow và hai tài liệu Phase 0 mới. Legacy server và lockfile gốc phải vẫn chạy nguyên trạng.
