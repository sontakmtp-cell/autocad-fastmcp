\# Phase 2 — FastMCP public facade local single-user



\## Tóm tắt



\- Baseline: `main` tại `f571f15`, worktree sạch; bộ legacy đạt `376 passed, 1 skipped`, spike FastMCP đạt `14 passed`.

\- Xây Gateway thật tại `services/gateway/`, nhưng chỉ chạy local một tiến trình và chỉ đọc bản vẽ.

\- Khóa đúng 3 public tools: `cad\_list\_devices`, `cad\_observe`, `cad\_query`.

\- Hoãn `cad\_get\_job` sang Phase 3 và `cad\_prepare\_program` sang Phase 6 để không đưa tool giả hoặc khóa sớm schema chưa có semantics thật.

\- Không triển khai SQLite, WebSocket, Desktop Agent, multi-user thật, write/preview/commit, production OAuth hay deployment.



\## Thay đổi triển khai



\### 1. Package Gateway và chế độ chạy



\- Tạo project độc lập `services/gateway/`, lock chính xác `fastmcp==3.4.4`; giữ dependency/lock của legacy tách biệt.

\- Chuyển những phần đã chứng minh trong spike sang code Gateway chính thức: outer Starlette app, `/healthz`, `/mcp`, FastMCP lifespan, auth adapter, host/origin guard và safe error mapping. Không import code production từ `poc/fastmcp-phase0`; spike được giữ nguyên làm bằng chứng hồi quy.

\- Composition root local sử dụng `CadApplicationService` của Phase 1 và backend hiện có qua `AUTOCAD\_MCP\_BACKEND=auto|file\_ipc|ezdxf`.

\- Với `ezdxf`, cho phép `AUTOCAD\_MCP\_PUBLIC\_V1\_DXF\_PATH` nạp một DXF local lúc khởi động; không nhận đường dẫn file qua MCP tool.

\- Thêm launcher local riêng đọc `AUTOCAD\_MCP\_INTERFACE=legacy|public\_v1`, mặc định `legacy` và từ chối `dual`. Không sửa hoặc thay thế launcher Phase 4 OAuth production hiện tại.



\### 2. Application service và snapshot



\- Tạo một device ổn định `local-default`; trạng thái và capabilities lấy từ backend thật, không dùng danh sách fixture hard-code.

\- `cad\_observe` gọi các read operation đã tách ở Phase 1 (`drawing.info`, entity reads và screenshot khi yêu cầu), chuẩn hóa dữ liệu rồi lưu immutable snapshot trong bộ nhớ.

\- `snapshot\_id` là UUID mới cho mỗi lần observe; `document\_revision` là SHA-256 của JSON chuẩn hóa gồm drawing metadata và entity records đã sắp xếp.

\- Entity public chỉ chứa `entity\_id`, `entity\_type`, `layer` và các trường hình học nằm trong allowlist; không trả raw backend/DXF object.

\- Snapshot, artifact và resource lookup luôn gắn với `principal.subject`. ID của principal khác trả `not\_found` để không làm lộ sự tồn tại.

\- `cad\_query` chỉ đọc snapshot đã lưu, lọc theo type/layer và phân trang ổn định; cùng snapshot, filter và cursor phải cho cùng kết quả.

\- Query mặc định 50, tối đa 100 entity/trang; tối đa 16 type và 16 layer/filter. Screenshot dùng `AUTOCAD\_MCP\_MAX\_IMAGE\_BYTES`, mặc định 5 MiB, và bị từ chối nếu vượt giới hạn.



\### 3. Public MCP contract `cad.mcp/1.0`



| Tool | Input chính | Output chính | Annotations |

| --- | --- | --- | --- |

| `cad\_list\_devices` | `online\_only=false`, optional `capability` | device summaries và `default\_device\_id` | read-only, idempotent, closed-world |

| `cad\_observe` | `device\_id`, `observation\_level=summary|detail`, `include\_preview\_image=false` | snapshot/revision, summary/entities URI, artifact refs | read-only, không idempotent |

| `cad\_query` | `snapshot\_id`, type/layer filters, cursor, limit | bounded entity page, total, next cursor và resource refs | read-only, idempotent |



\- Mọi input model dùng Pydantic strict với `extra="forbid"`; mọi output có `contract\_version` và `correlation\_id`.

\- `structuredContent` phải khớp `outputSchema`; `content` chỉ chứa tóm tắt ngắn và resource links, không chứa token, đường dẫn nội bộ hay blob lớn.

\- Dùng correlation middleware FastMCP với request-scoped `ContextVar`: tạo UUID tại đầu request, truyền qua service/log/output và reset trong `finally`.

\- Error public chỉ gồm mã an toàn: `invalid\_request`, `not\_found`, `backend\_error`, `response\_too\_large`, `internal\_error`; lỗi nội bộ kèm correlation ID nhưng không lộ exception/path.

\- Đăng ký bốn resource templates:

&#x20; - `cad://devices/{device\_id}/capabilities`

&#x20; - `cad://snapshots/{snapshot\_id}/summary`

&#x20; - `cad://snapshots/{snapshot\_id}/entities{?cursor,limit,types,layers}`

&#x20; - `cad://artifacts/{artifact\_id}`

\- Đăng ký hai prompt thử nghiệm `plan\_cad\_change` và `repair\_after\_validation`; trong Phase 2 chúng chỉ hướng dẫn quan sát, query và lập kế hoạch, đồng thời phải dừng trước mọi thay đổi bản vẽ.

\- Metadata tuân theo hướng dẫn OpenAI hiện hành: mỗi tool làm một việc, input/output rõ ràng, read và write tách biệt; annotations chỉ là gợi ý cho ChatGPT, enforcement vẫn thuộc server. \[OpenAI tool-design guidance](https://developers.openai.com/apps-sdk/plan/tools), \[Apps SDK reference](https://developers.openai.com/apps-sdk/reference).



\### 4. Bảo mật và tương thích



\- Local no-auth chỉ được bind loopback và dùng principal cố định `local-single-user`; no-auth trên địa chỉ ngoài loopback phải fail startup.

\- Giữ đường JWT/Auth fixture với scope `autocad.read` để test boundary, nhưng không kết nối Auth0 production trong Phase 2.

\- Public facade không đăng ký bất kỳ legacy tool, primitive, raw LISP, write tool hay tên tool Phase 3+.

\- `legacy` tiếp tục là mặc định và phải giữ nguyên output/test. Rollback chỉ cần đổi interface về `legacy`; không có migration hay dữ liệu bền vững cần hoàn tác.

\- Ghi evidence Phase 2 và cập nhật roadmap để lưu quyết định “3 read tools”; không sửa lại lịch sử Phase 0.



\## Kiểm thử và tiêu chí hoàn thành



\- Unit test application service: backend success/failure, revision digest ổn định, snapshot immutability, filters, cursor, giới hạn trang và screenshot quá cỡ.

\- Ownership tests với hai principal: không đọc chéo device, snapshot hoặc artifact; request bị từ chối không được gọi backend.

\- FastMCP contract snapshots phải có đúng 3 tools, 4 resource templates và 2 prompts; schema/annotations thay đổi ngoài dự kiến làm CI fail.

\- In-memory Client và MCP Streamable HTTP client chạy đầy đủ `initialize → list → cad\_list\_devices → cad\_observe → cad\_query → resource read` ở stateless và stateful mode.

\- Test malformed/extra input, unknown IDs, masked exceptions, Host/Origin, JWT thiếu hoặc sai scope và concurrent correlation IDs.

\- Golden prompt rehearsal:

&#x20; - “Liệt kê máy CAD” chỉ chọn `cad\_list\_devices`.

&#x20; - “Quan sát rồi liệt kê LINE trên layer 0” chạy `cad\_observe` rồi `cad\_query`.

&#x20; - Yêu cầu xóa/sửa/chạy LISP không tìm thấy public write tool và không rơi về legacy primitive.

\- CI Gateway chạy Windows và Linux trên Python 3.10/3.12/3.13, kiểm exact FastMCP version và snapshot diff.

\- Regression gates:

&#x20; - Legacy suite tiếp tục xanh từ baseline `376 passed, 1 skipped`.

&#x20; - Phase 0 spike tiếp tục `14 passed`.

&#x20; - Gateway suite xanh, `git diff --check` sạch và build package thành công.

\- Demo chấp nhận cuối phase dùng `AUTOCAD\_MCP\_BACKEND=ezdxf` cùng DXF fixture thật; client chỉ nhìn thấy ba public tools và query được entity theo type/layer.

\- Không yêu cầu ChatGPT Web, VPS, tunnel hay AutoCAD thật để đóng Phase 2; những luồng đó thuộc Phase 3–4.



\## Giả định đã khóa



\- Tool surface là phương án Khầy chọn: đúng 3 tool đọc.

\- `fastmcp==3.4.4` giữ nguyên exact pin.

\- Public contract bắt đầu tại `cad.mcp/1.0`; tool bổ sung sau này là thay đổi additive có version mới phù hợp.

\- OpenAI Platform API key không cần cho Phase 2; không tạo hoặc ghi `OPENAI\_API\_KEY`.

\- Không deploy và không thay đổi cấu hình OAuth production hiện tại.

## Phase 2.1 — Observation, revision and resource lifecycle hardening

Phase 2 gốc giữ nguyên public surface và lịch sử ở trên. Phase 2.1 là một security
and correctness hardening pass, không thêm capability mới:

- `document_revision` dùng payload có version `cad.revision/1`, luôn dựa trên
  canonical drawing state và entity detail bất kể client yêu cầu `summary` hay
  `detail`;
- local observation có entity/detail-call/deadline/normalized-byte budgets;
- local snapshot store dùng TTL, maximum count, maximum aggregate bytes và
  oldest-first eviction; artifact có index trực tiếp và cùng lifecycle/owner với
  snapshot;
- preview chỉ materialize từ attachment `image/png` có base64, PNG signature và
  decoded-size hợp lệ;
- Host được parse như authority chính xác, Origin có header phải nằm trong
  explicit allowlist, và outer guard vẫn chạy trước FastMCP session handling;
- tool/resource errors trả safe code, safe summary và request correlation ID;
- malformed backend values fail closed; device chỉ online khi backend xác nhận
  document, reachability và trạng thái không busy/modal;
- filters được trim, deduplicate và canonicalize; cursor `cad.cursor/1` chứa hash
  filter bounded thay vì lặp toàn bộ query.

Không đổi `cad.mcp/1.0`, ba tool, bốn resource templates, hai prompts hay schema
snapshots. Profile `phase3_poc` vẫn dùng `cad.mcp/1.1`, SQLite và Agent transport
riêng. Chi tiết implementation, test evidence, giới hạn và deferred decisions nằm
tại `docs/architecture/phase2.1-observation-hardening-evidence.md`.


