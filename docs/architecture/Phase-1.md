# Kế hoạch thực hiện Phase 1 — Tách CAD service khỏi MCP handler

## Tóm tắt

- Kiến trúc được chọn là `tool-only`: giữ nguyên MCP server hiện tại, không thêm widget/UI.
- Mục tiêu là biến luồng hiện tại thành `MCP handler → compatibility adapter → CadApplicationService → backend`, đúng [Phase 1 trong tài liệu kiến trúc](/D:/AI/autocad-mcp/docs/architecture/fastmcp-multi-user-autocad-plan.md:1185).
- Không đổi tên, chữ ký, schema, annotation hay JSON/image response của 16 tool legacy. OpenAI xem tool là contract giữa MCP và model, nên Phase 1 chỉ đổi phần triển khai phía sau contract. [Define tools](https://developers.openai.com/apps-sdk/plan/tools/), [Apps SDK reference](https://developers.openai.com/apps-sdk/reference/).
- Baseline vừa kiểm tra: root suite `302 passed, 1 skipped`; FastMCP POC `14 passed`.
- Cổng vào bắt buộc: chỉ bắt đầu code Phase 1 sau khi Phase 0 có đủ sáu CI job Windows/Linux × Python 3.10/3.12/3.13 xanh và [báo cáo Phase 0](/D:/AI/autocad-mcp/docs/architecture/phase0-fastmcp-compatibility-evidence.md:1) được đổi từ `NO-GO` sang `GO`.

## Thay đổi triển khai

1. **Tạo package core độc lập**
   - Tạo `packages/cad_core` với `pyproject.toml` riêng, chỉ dùng Python chuẩn; thêm nó như local path dependency cho root project và FastMCP POC, rồi cập nhật hai lockfile.
   - Public API nội bộ gồm:
     - `CadInvocation`: nhóm tool, operation, arguments và yêu cầu screenshot.
     - `CadServiceResponse`: `CommandResult` cùng attachment trung lập.
     - `CadImageAttachment`: MIME và dữ liệu PNG base64, không chứa MCP `ImageContent`.
     - `CadRuntimePort`: contract cấu trúc cho các backend operation hiện đang được handler gọi.
     - `AdvancedAnnotationPort`: seam riêng cho dimension workflow.
     - `CadApplicationService.execute(invocation)`.
   - Chuyển `CommandResult` và `BackendCapabilities` vào core nhưng re-export tại `autocad_mcp.backends.base`, để toàn bộ import cũ tiếp tục hoạt động.
   - Core tuyệt đối không import MCP/FastMCP, Starlette, OAuth, remote policy, COM hoặc `autocad_mcp.server`.

2. **Bọc backend hiện tại bằng runtime adapter**
   - `LegacyRuntimeAdapter` triển khai `CadRuntimePort` bằng cách ủy quyền 1:1 sang `get_backend()` và `AutoCADBackend`; không sửa thuật toán File IPC, ezdxf, P&ID, screenshot hoặc dimension.
   - `CadApplicationService` giữ bảng dispatch của tám nhóm `drawing/entity/layer/block/annotation/pid/view/system`.
   - Các operation phụ thuộc MCP/process như `system.runtime` và `system.tool_manifest` tiếp tục nằm ở compatibility adapter; backend calls của `status`, `health`, `init`, `execute_lisp` đi qua service.
   - Thiếu field tiếp tục phát sinh `KeyError/ValueError` như hiện tại; operation không tồn tại trả `UnknownCadOperation` để legacy adapter chuyển về đúng JSON cũ.

3. **Làm mỏng legacy MCP handlers**
   - Giữ nguyên decorator, chữ ký và mô tả tại [server.py](/D:/AI/autocad-mcp/src/autocad_mcp/server.py:169); thân handler chỉ tạo `CadInvocation`, gọi service và format response.
   - `_safe`, OAuth scope, remote policy, audit, host/origin và giới hạn kích thước ảnh vẫn ở MCP boundary.
   - Compatibility formatter chuyển `CadServiceResponse` thành đúng JSON compact hoặc `TextContent + ImageContent` hiện tại; `view.get_screenshot`, `ONLY_TEXT_FEEDBACK`, invalid base64 và remote image limit phải giữ nguyên hành vi.
   - Không tạo production `dual` mode hay env flag mới. “Dual adapter” chỉ là test harness gọi legacy MCP adapter và FastMCP POC bằng cùng fake runtime.

4. **Cô lập dimension monkey patches**
   - Không viết lại thuật toán dimension hoặc các phase performance/ActiveX/scope.
   - `LegacyAdvancedAnnotationAdapter` chỉ resolve `_run_annotation` sau `register_optional_features()` và tại thời điểm gọi, để toàn bộ patch chain hiện tại vẫn có hiệu lực.
   - Chuyển riêng phần response của dimension runner sang `CadServiceResponse`; preview/plan/commit/audit/repair logic và các store hiện tại không đổi.
   - Dedicated `annotation_*` tools và consolidated `annotation` đều đi qua cùng service seam.
   - `cad_core` chỉ thấy `AdvancedAnnotationPort`, không thấy module đăng ký FastMCP hoặc các biến global dimension.

5. **Nối FastMCP Phase 0 vào seam chung**
   - `cad_observe` của POC dùng `CadApplicationService` với runtime ezdxf/fake thay vì gọi backend trực tiếp.
   - `cad_list_devices` và `cad_get_job` vẫn là fake Phase 0; không đưa device/job/persistence vào Phase 1.
   - Demo bắt buộc chứng minh legacy `drawing.info/view.get_screenshot` và FastMCP `cad_observe` đi qua cùng service/runtime instance và cho kết quả tương đương.

## Kiểm thử và tiêu chí hoàn thành

- Đóng băng snapshot descriptor của toàn bộ 16 tool trước khi đổi code; sau đổi phải giống hoàn toàn về tên, input schema, description và annotations.
- Dùng bảng test tham số hóa bao phủ mọi nhánh operation của tám nhóm, kiểm đúng backend method, thứ tự positional arguments, defaults và kết quả JSON.
- Golden tests cho:
  - backend success/failure;
  - unknown operation, thiếu field và exception;
  - health error mapping;
  - screenshot thường, direct screenshot, text-only, invalid base64 và quá giới hạn;
  - advanced dimension patch chain, plan/commit metadata và preview image.
- Unit test core bằng fake ports và chặn import `mcp`, `fastmcp`, `starlette`, `pywin32`, `autocad_mcp.server`; service phải khởi tạo và chạy mà không đăng ký MCP.
- Protocol regression: stdio và Streamable HTTP vẫn list/call cùng manifest; OAuth/remote-policy tests vẫn giữ nguyên.
- Chạy:
  - toàn bộ root suite, không được thấp hơn baseline `302 passed, 1 skipped`;
  - toàn bộ FastMCP POC suite, gồm test seam mới;
  - CI Windows/Linux × Python 3.10/3.12/3.13;
  - `git diff --check`.
- Phase 1 đạt khi mọi kiểm thử xanh, legacy output không drift, cả hai facade dùng chung service, và service tests không khởi động/import MCP.

## Giả định và ranh giới

- Không sửa annotation legacy dù tài liệu OpenAI hiện yêu cầu `readOnlyHint`, `destructiveHint` và `openWorldHint` chính xác; việc sửa public descriptor thuộc Phase 2/public v1.
- Không thêm Gateway production, WebSocket, database, job, device ownership, Desktop Agent hoặc CAD Program.
- Không thay OAuth Phase 4, launcher production, remote policy, LISP hay cấu hình deploy.
- Không cần AutoCAD thật cho acceptance Phase 1; ezdxf, fake runtime và regression File IPC là đủ.
- Không deploy sau khi hoàn thành; rollback bằng cách revert commit chuyển routing sang service, trong khi backend và legacy entrypoint vẫn còn nguyên.

## Post-implementation hardening (Phase 1.1)

Phase 1.1 was added after Phase 1–3 to close three implementation debts without
rewriting those phases:

- the internal `cad_core` import is now distributed by the project-specific
  `autocad-cad-core` wheel and installed beside the `autocad-mcp` wheel from a
  local artifact directory;
- Phase 4 read capabilities use explicit typed port methods instead of backend
  method-name strings and positional `*args`;
- a test-only shared-runtime harness compares the legacy compatibility path and
  the FastMCP/public facade path, while production continues to expose only one
  selected facade at a time.

The implementation and hosted results are recorded in
[`phase1.1-cad-core-hardening-evidence.md`](phase1.1-cad-core-hardening-evidence.md).
The original Phase 1 conclusions above remain historical context.
