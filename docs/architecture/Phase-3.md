# Kế hoạch thực hiện Phase 3 — Durable Gateway + simulated outbound Agent

> Trạng thái: kế hoạch triển khai, chưa sửa code sản phẩm và chưa deploy.
>
> Baseline khảo sát: `main` tại `3b1cc5b` (`Phase 2 done`), worktree sạch ngày 2026-07-21.
>
> Phạm vi Phase 3 này là POC B trong kiến trúc multi-user, không phải launcher OAuth Phase 3 cũ.

## 1. Kết quả cần đạt

Phase 3 biến Gateway Phase 2 từ luồng đọc trực tiếp trong một process thành một POC có hàng đợi bền vững:

```text
MCP cad_observe
    -> ghi job vào SQLite
    -> route command qua /agent/ws
    -> Agent giả lập ack/progress/result
    -> lưu snapshot và lịch sử job
    -> cad_get_job đọc lại được sau reconnect/restart
```

Phase 3 đạt khi chứng minh được sáu điều:

1. Gateway route đúng command đến đúng một trong hai device giả lập.
2. SQLite là nguồn sự thật cho device, session, job, event và snapshot; restart Gateway không làm mất job đã ghi.
3. Mất socket, heartbeat cũ, timeout, cancel và reconnect/reconcile đưa job về đúng trạng thái.
4. Gửi lại cùng command không thực thi hai lần; cùng ID nhưng payload khác bị từ chối.
5. FastMCP vẫn là lớp giao tiếp mỏng; WebSocket, SQLite và state machine không import FastMCP.
6. Legacy server, OAuth production, AutoCAD runtime và ba tool đọc Phase 2 không bị phá vỡ.

Baseline hiện tại đã kiểm tra lại:

- Gateway Phase 2: `22 passed`.
- FastMCP Phase 0 spike: `14 passed`.
- Legacy root: `376 passed, 1 skipped, 9 warnings`.
- `git diff --check`: sạch.

## 2. Quyết định cần khóa trước khi viết code

### 2.1. Public MCP contract

Đề xuất tăng contract theo kiểu cộng thêm từ `cad.mcp/1.0` lên `cad.mcp/1.1`:

- Giữ nguyên `cad_list_devices`, `cad_observe` và `cad_query`.
- Thêm đúng một public tool: `cad_get_job`.
- Thêm resource template `cad://jobs/{job_id}`.
- Chưa expose `cad_cancel_job`; Phase 3 chỉ kiểm cancel qua application service và protocol nội bộ.
- `cad_observe` vẫn trả snapshot như Phase 2 khi thành công, đồng thời bổ sung field tùy chọn `job_id`. Nó không bị đổi thành một tool “fire-and-forget” trả mỗi job ID.
- Trong durable profile, `cad_observe` phải tạo job trước, chờ kết quả trong một khoảng hữu hạn rồi mới materialize snapshot. Nếu chưa xong, lỗi an toàn phải kèm job ID để client tiếp tục bằng `cad_get_job`.

Cách này giữ luồng người dùng Phase 2 đơn giản, đồng thời mở được progress/reconnect mà không đổi nghĩa tool cũ.

Contract `cad_get_job` đề xuất:

- Input: `job_id`, optional opaque `event_cursor`, `event_limit` mặc định 50 và tối đa 100.
- Output: `contract_version`, `correlation_id`, `job_id`, `device_id`, `kind`, `state`, bounded progress, result/resource refs hoặc safe error, ordered events và `next_event_cursor`.
- Annotation: read-only, idempotent, non-destructive, closed-world; scope `autocad.read`.
- Job/resource của owner khác luôn trả `not_found`, không phân biệt “không tồn tại” với “không có quyền”.

Public error delta chỉ thêm các mã cần cho POC: `device_offline`, `capability_missing`, `job_in_progress`, `deadline_expired`, `dispatcher_timeout` và `idempotency_conflict`. `outcome_unknown` là job state được đọc qua `cad_get_job`, không bị biến thành một generic MCP error.

### 2.2. Profile chạy

Gateway có hai profile rõ ràng:

- `local`: hành vi Phase 2, backend local/in-process, tiếp tục là mặc định trong lúc POC.
- `phase3_poc`: SQLite + Agent transport + simulator, không gọi AutoCAD backend.

Profile POC phải fail startup nếu thiếu DB path hoặc fixture device authenticator. Không có profile tự động đoán cấu hình và không có chế độ chạy đồng thời local backend với Agent transport.

### 2.3. Auth của Agent giả lập

Phase 3 chưa làm pairing hay device credential thật. Dùng một `DeviceAuthenticator` port và implementation fixture chỉ bật trong `phase3_poc`:

- Fixture token map tới đúng một `device_id` đã seed.
- `hello.device_id` phải khớp device đã xác thực; không tin `device_id` trong JSON một cách độc lập.
- Token fixture không hard-code trong source, không log và không được phép dùng trong profile production tương lai.
- Device key, challenge, rotation và revoke thật thuộc Phase 5.

### 2.4. WebSocket và TLS

Application expose `/agent/ws`; Agent luôn là phía mở kết nối outbound. Ở production, reverse proxy terminate TLS để đường ngoài là `wss://.../agent/ws`.

Phase 3 phải có:

- ASGI/WebSocket tests không cần network thật cho phần lớn failure matrix.
- Một integration test chạy Uvicorn loopback với certificate test và Agent kết nối `wss://localhost`, để không gọi một kết nối `ws://` thuần là bằng chứng WSS.
- Chưa cấu hình domain, certificate thật, Caddy/Nginx hay VPS.

## 3. Ranh giới phạm vi

### Trong Phase 3

- Shared typed contract `cad.agent/1`.
- SQLite migration, repository và backup/restore test.
- Job state machine, idempotency, event cursor và reconciliation.
- In-memory connection registry cho một Gateway worker.
- `/agent/ws`, heartbeat, dispatch, progress/result và cancel nội bộ.
- Agent giả lập chạy như một process/client độc lập, có failure injection.
- Hai device fixture thuộc một test principal.
- `cad_get_job`, job resource và schema snapshots mới.
- Read-only command `observe`; một write-like fixture chỉ dùng để chứng minh `outcome_unknown` không bị retry.

### Không trong Phase 3

- Desktop Agent Windows thật, tray UI, installer hoặc auto-update.
- AutoCAD, COM, File IPC, AutoLISP, screenshot thật hoặc CAD Program.
- Auth0/ChatGPT login production, pairing, device key thật hoặc multi-user isolation hoàn chỉnh.
- Public CAD write, preview, commit, approval, rollback hoặc `cad_cancel_job`.
- Artifact upload lớn, signed URL hoặc object storage.
- Postgres, Redis/NATS, S3, nhiều Gateway process/worker.
- Deploy VPS hoặc sửa `scripts/run-phase4-oauth.ps1`, `start_mcp_chatgpt.bat` và legacy entrypoint.

## 4. Cấu trúc code đề xuất

Chỉ tách phần mới; không di chuyển hàng loạt code Phase 2 đang ổn định.

```text
packages/contracts/
  pyproject.toml
  src/autocad_contracts/
    __init__.py
    agent_protocol.py

services/gateway/src/autocad_gateway/
  app.py                         # thêm cad_get_job, job resource, WS/ready routes
  contracts.py                   # public cad.mcp/1.1 DTO
  composition.py                 # build local hoặc phase3_poc profile
  durable_services.py            # facade cho Device/Observation/Query/Job service
  domain/
    jobs.py                      # state, transition và invariant
  application/
    job_service.py
    device_service.py
    observation_service.py
  infrastructure/
    sqlite/
      database.py
      repositories.py
      migrations/0001_phase3.sql
    agent_transport/
      authenticator.py
      connection_registry.py
      dispatcher.py
      websocket_endpoint.py

poc/phase3-simulated-agent/
  pyproject.toml
  uv.lock
  src/autocad_phase3_sim_agent/
    __init__.py
    __main__.py
    agent.py
    scenarios.py
  tests/

services/gateway/tests/
  test_job_state_machine.py
  test_sqlite_repositories.py
  test_agent_protocol.py
  test_agent_websocket.py
  test_dispatch_reconcile.py
  test_phase3_mcp_flow.py
  test_gateway_restart.py
```

`services.py` hiện tại tiếp tục giữ local Phase 2. `durable_services.py` triển khai cùng application-facing interface để `app.py` không chứa nhánh SQLite/WebSocket. Không đưa decorator hoặc `Context` của FastMCP vào `domain/`, `application/`, `infrastructure/` hay `packages/contracts/`.

## 5. Contract Gateway–Agent `cad.agent/1`

`packages/contracts` chỉ phụ thuộc Pydantic/Python chuẩn và không import Gateway, AutoCAD hoặc FastMCP. Mọi model dùng strict validation, `extra="forbid"` và giới hạn kích thước chuỗi/list.

Envelope chung có:

- `protocol_version`, `message_type`, `message_id`, `correlation_id`.
- `session_id`, `device_id`, `job_id`, `command_id` khi message cần.
- `sequence`, `issued_at`, `deadline_at`.
- `idempotency_key`, `payload_hash` cho command.

Message runtime Phase 3:

| Message | Hướng | Vai trò |
| --- | --- | --- |
| `hello` | Agent → Gateway | Protocol min/max, fixture proof, device, capability hash và last processed sequence. |
| `welcome` | Gateway → Agent | Version được chọn, session ID, heartbeat interval và server time. |
| `heartbeat` | Hai chiều | Presence, busy state, last processed sequence và current job. |
| `command` | Gateway → Agent | Phase 3 chỉ cho `observe`; write-like command chỉ tồn tại trong test fixture. |
| `ack` | Agent → Gateway | `accepted`, `duplicate`, `rejected`, `already_terminal`. |
| `progress` | Agent → Gateway | Sequence tăng dần, phase, percent và message bị giới hạn. |
| `result` | Agent → Gateway | Terminal result hoặc error taxonomy; không mang blob lớn. |
| `cancel` | Gateway → Agent | Cooperative cancel, không giả định đã dừng cho tới khi có result. |
| `reconcile` | Gateway → Agent | Hỏi ledger của các command chưa có kết luận sau reconnect. |
| `reconcile_result` | Agent → Gateway | `not_started`, `started`, `terminal` cùng hash/result đã biết. |
| `error` | Hai chiều | Lỗi auth/protocol/schema; không thay terminal job result. |

Quy tắc bắt buộc:

- Gateway tự canonicalize payload và tính lại SHA-256; không tin `payload_hash` do bên kia gửi.
- Cùng `command_id`/idempotency key và cùng hash trả record cũ; khác hash trả `payload_mismatch`.
- Message hết deadline trước khi start bị từ chối.
- Sequence progress/event chỉ tăng; duplicate giống hệt được bỏ qua an toàn, out-of-order hoặc nội dung khác bị audit/reject.
- Không negotiate được `cad.agent/1` thì session thành `incompatible` và không nhận command.
- Max WebSocket message size, heartbeat interval, stale threshold và queue size đều có giới hạn cấu hình.

## 6. SQLite và repository

Migration đầu tiên chỉ tạo phần Phase 3 cần:

| Bảng | Dữ liệu chính |
| --- | --- |
| `schema_migrations` | version/checksum/applied time. |
| `devices` | owner subject, display name, status, capabilities, fixture-auth reference và timestamps. |
| `agent_sessions` | device, connection/session ID, protocol version, connected/heartbeat/disconnected time và last sequence. |
| `jobs` | owner/device, kind, effect class read/write, state, state version, deadline, command/idempotency/payload hash, result/error summary và timestamps. |
| `job_events` | job + monotonic event sequence, state/progress/error/result summary và time. |
| `snapshots` | owner/device/job, immutable revision, bounded drawing/entity JSON và created time. |

Quy tắc DB:

- Bật `WAL`, foreign keys, busy timeout và short transactions.
- Không giữ transaction trong lúc chờ WebSocket, heartbeat hoặc Agent result.
- Transition dùng compare-and-swap bằng `state_version`; concurrent handler chỉ có một bên thắng.
- `job_events(job_id, sequence)` và command/idempotency identity có unique constraint.
- User-facing repository method luôn nhận `owner_subject`; không có `get_job(job_id)` trần ở application layer.
- Result/event chỉ lưu bounded JSON và resource refs; không lưu token, full path hoặc base64.
- Startup migration idempotent; checksum lệch phải fail readiness, không tự sửa âm thầm.
- Backup dùng SQLite backup API; test restore mở một Gateway instance mới và đọc được cùng job/events/snapshot.

## 7. Job state machine

Phase 3 triển khai các state cần cho POC:

```text
queued -> dispatched -> acknowledged -> running -> succeeded|failed
   |          |              |            |
   |          +-> reconnect_pending        +-> cancel_requested -> cancelled|succeeded
   |                         |
   +-> cancelled             +-> queued|succeeded|failed|needs_attention

acknowledged|running -- mất kết nối với effect_class=write --> outcome_unknown
outcome_unknown -- reconcile --> succeeded|failed|needs_attention
```

Invariant:

- Terminal state không được chuyển tiếp hoặc ghi đè result.
- Read chưa start có thể quay lại `queued` sau reconcile.
- Write-like fixture đã `started` nhưng mất result phải vào `outcome_unknown`, tuyệt đối không redispatch tự động.
- `outcome_unknown` chỉ thoát khi reconcile có bằng chứng; không đủ bằng chứng thì `needs_attention`.
- Cancel ở `queued` kết thúc ngay; sau dispatch phải gửi `cancel` và chờ Agent; result thành công đến trước cancel vẫn được ghi `succeeded`.
- Deadline sweeper chỉ đổi state bằng CAS và tạo event audit tương ứng.

## 8. Luồng ứng dụng và ASGI lifecycle

### 8.1. Startup/shutdown

`phase3_poc` startup theo thứ tự:

1. Validate profile, loopback/test TLS, DB path, fixture authenticator và limits.
2. Mở SQLite, set pragmas, chạy migration và kiểm checksum.
3. Mark session cũ disconnected; chuyển job chưa terminal sang trạng thái recovery phù hợp.
4. Khởi tạo connection registry, dispatcher, heartbeat/deadline sweeper.
5. Khởi tạo FastMCP lifespan và mở readiness.

Shutdown dừng nhận job mới, cancel background tasks có kiểm soát, đánh dấu session disconnected và đóng DB. Không xóa DB hay tự đổi job terminal.

### 8.2. Routes

- `/healthz`: process còn sống; không phụ thuộc device online.
- `/readyz`: DB mở, migration đúng và dispatcher chạy.
- `/mcp`: FastMCP Streamable HTTP hiện tại.
- `/agent/ws`: WebSocket cho Agent outbound; route phải đứng trước catch-all `Mount` của MCP.

### 8.3. Normal observe flow

1. `cad_observe` xác thực principal và ownership device.
2. `ObservationService` ghi `queued` job trong transaction ngắn.
3. Dispatcher claim job bằng CAS, lấy socket đúng device và ghi `dispatched` trước khi send.
4. Agent ghi ledger giả lập rồi gửi `ack`, progress và result.
5. Gateway validate order/hash, ghi events và snapshot, rồi chuyển job terminal trong transaction ngắn.
6. `cad_observe` trả output Phase 2 + `job_id`; `cad_get_job` và `cad://jobs/{job_id}` đọc lại lịch sử/result owner-filtered.

## 9. Agent giả lập và failure injection

Simulator phải là process/client độc lập, chỉ import `autocad_contracts`; không import `autocad_gateway`, `autocad_mcp`, ezdxf, COM hay AutoLISP.

Nó có ledger in-memory hoặc SQLite tạm cho mỗi test và các scenario xác định được:

- `success`.
- `delay_before_ack` và `delay_result`.
- `drop_before_ack`.
- `drop_after_ack_before_start`.
- `drop_after_start_before_result`.
- `duplicate_ack`, `duplicate_progress`, `duplicate_result`.
- `out_of_order_progress`.
- `payload_hash_mismatch`.
- `stale_heartbeat`.
- `reconnect_not_started`, `reconnect_started`, `reconnect_terminal`.
- `cancel_before_start`, `cancel_while_running`, `cancel_too_late`.

Failure injection chỉ nhận từ CLI/test fixture khi khởi động simulator, không có MCP tool hoặc public HTTP endpoint để model thay đổi scenario.

## 10. Thứ tự triển khai theo file và gate

### Bước 0 — Khóa baseline và contract delta

**File:** `docs/architecture/Phase-3.md`, `services/gateway/snapshots/*.json`.

- Chụp manifest Phase 2 hiện tại và tạo expected diff `3 tools -> 4 tools`, `4 resources -> 5 resources`, prompts giữ nguyên 2.
- Chốt `cad.mcp/1.1`, `cad_get_job` schema, `job_id` optional trên observe và error taxonomy mới.

**Gate:** chưa viết transport/DB nếu contract delta chưa được review; không có tool write xuất hiện.

### Bước 1 — Shared protocol contract

**File:** `packages/contracts/**`, `services/gateway/pyproject.toml`, `services/gateway/uv.lock`.

- Tạo strict models, canonical payload hash và version negotiation.
- Gateway nhận package này qua local path dependency.
- Thêm direct dependency cho async SQLite/WebSocket cần dùng; vẫn pin chính xác `fastmcp==3.4.4`.

**Gate:** contract tests chạy mà không import Gateway/FastMCP/AutoCAD; malformed, oversized, unsupported version và hash mismatch đều bị từ chối.

### Bước 2 — State machine và SQLite

**File:** `domain/jobs.py`, `infrastructure/sqlite/**`, `test_job_state_machine.py`, `test_sqlite_repositories.py`.

- Viết transition tests trước, sau đó repository/migration để làm chúng pass.
- Thêm owner filter, CAS, idempotency, event pagination, backup/restore.

**Gate:** migration chạy hai lần không đổi dữ liệu; concurrent claim chỉ một winner; restart/restore đọc lại đúng terminal và non-terminal jobs.

### Bước 3 — Job/Device/Observation services

**File:** `application/**`, `durable_services.py`.

- Application services chỉ dùng repository/dispatcher ports.
- Tạo job trước dispatch; snapshot chỉ materialize từ result hợp lệ.
- Local Phase 2 service chưa bị route qua SQLite.

**Gate:** unit tests dùng fake repositories/dispatcher, không cần ASGI/FastMCP/socket.

### Bước 4 — Connection registry và WebSocket endpoint

**File:** `infrastructure/agent_transport/**`, `app.py`.

- Authenticate fixture, hello/welcome, one active socket per device, heartbeat/stale handling.
- Send/receive ngoài DB transaction; bounded per-device queue; disconnect cleanup không xóa durable state.

**Gate:** hai Agent nối đồng thời; command cho device A không tới B; connection cũ bị thay thế có chủ đích và audit, không race silent.

### Bước 5 — Dispatcher, reconcile và cancel nội bộ

**File:** `dispatcher.py`, `job_service.py`, `test_dispatch_reconcile.py`.

- Claim/send/ack/progress/result theo CAS.
- Recovery startup và reconnect ledger reconciliation.
- Deadline/heartbeat sweeper và cooperative cancel.

**Gate:** toàn bộ failure matrix cho kết quả xác định; write-like started job không bị gửi lại.

### Bước 6 — Simulator độc lập

**File:** `poc/phase3-simulated-agent/**`.

- Implement outbound client, fixture proof, ledger và scenario flags.
- Có CLI tối thiểu cho device ID, Gateway URL, fixture token và scenario.

**Gate:** wheel/test package độc lập; dependency graph không kéo AutoCAD/Gateway/FastMCP.

### Bước 7 — Nối public MCP

**File:** `contracts.py`, `app.py`, `composition.py`, `__main__.py`, snapshots và contract tests.

- Đăng ký `cad_get_job`, job resource và safe error mapping.
- `cad_list_devices` đọc SQLite/registry trong POC profile.
- `cad_observe` đi qua durable job, `cad_query` đọc persisted snapshot.
- Giữ local profile và legacy entrypoint nguyên hành vi.

**Gate:** FastMCP Client và MCP SDK client đều chạy `list -> observe -> get_job -> query -> read resources`; schema diff chỉ chứa delta đã duyệt.

### Bước 8 — Restart, WSS, CI và evidence

**File:** tests restart/WSS, `.github/workflows/phase2-gateway.yml`, `services/gateway/README.md`, `docs/architecture/phase3-durable-gateway-evidence.md`.

- Chạy live WSS loopback test bằng certificate test.
- Chạy matrix Windows/Linux × Python 3.10/3.12/3.13 cho unit/ASGI/SQLite; live-process WSS ít nhất trên Python 3.12 của hai OS.
- Build Gateway, contracts và simulator; kiểm lock/snapshot diff.
- Ghi evidence gồm test matrix, normal demo, failure matrix, DB restore và giới hạn chưa làm.

**Gate:** evidence được duyệt trước Phase 4/AutoCAD thật.

## 11. Ma trận kiểm thử bắt buộc

| Nhóm | Ca bắt buộc |
| --- | --- |
| Domain | Mọi transition hợp lệ/không hợp lệ, terminal immutability, CAS conflict, read retry và write unknown. |
| Repository | Migration/checksum, FK, owner filter, event cursor, duplicate key/hash, concurrent claim, backup/restore. |
| Protocol | Strict envelope, version mismatch, deadline, bounds, sequence, duplicate và payload mismatch. |
| WebSocket | Auth fixture, hello/welcome, heartbeat, stale, two devices, replacement/reconnect và WSS loopback. |
| Dispatcher | No socket, drop trước/sau ack/start, duplicate messages, cancel, timeout và reconcile outcomes. |
| MCP contract | Đúng 4 tools, 5 resources, 2 prompts; annotations và schema snapshots; không có write/legacy/LISP tool. |
| End-to-end | MCP `cad_observe` -> SQLite -> Agent giả -> progress/result -> snapshot -> `cad_get_job` -> query/resource. |
| Restart | Restart khi queued/dispatched/running/terminal; không mất event, không duplicate effect, readiness đúng. |
| Regression | Gateway Phase 2 local flow, Phase 0 spike, toàn bộ legacy suite và `git diff --check`. |

## 12. Demo nghiệm thu

Demo dùng một Gateway `phase3_poc`, một DB tạm và hai simulator `device-a`/`device-b`:

1. Cả hai Agent connect outbound và `cad_list_devices` thấy đúng hai device online.
2. Gọi `cad_observe(device-a)`; chỉ Agent A nhận command, trả ít nhất hai progress events và một result.
3. `cad_get_job` trả ordered events; `cad_query` đọc snapshot đã persist.
4. Restart Gateway bằng cùng DB; reconnect hai Agent; job/snapshot cũ vẫn đọc được.
5. Chạy `drop_after_ack_before_start`; Agent reconnect báo `not_started`, Gateway dispatch lại đúng một effect.
6. Chạy write-like test fixture `drop_after_start_before_result`; job thành `outcome_unknown`, không tự chạy lại, rồi reconcile thành `needs_attention` nếu không đủ bằng chứng.
7. Làm Agent B stale heartbeat; device B offline nhưng health vẫn xanh, readiness vẫn phụ thuộc DB/dispatcher chứ không phụ thuộc device.
8. Backup DB, restore sang DB mới và xác nhận cùng job/event/snapshot.

## 13. Definition of Done Phase 3

Phase 3 chỉ được đánh dấu hoàn tất khi:

- Public surface là đúng 4 read tools, 5 resource templates, 2 prompts; không lộ primitive/write/LISP.
- Normal observe thực sự đi qua durable job và socket Agent giả, không gọi local CAD backend trong `phase3_poc`.
- SQLite là truth; registry chỉ giữ socket/presence tạm trong RAM.
- Timeout/cancel/reconnect/reconcile/stale heartbeat/two-device/duplicate matrix đều có test xanh.
- Gateway restart và SQLite backup/restore không mất job/events/snapshot.
- `outcome_unknown` không tự retry write-like effect.
- Không có DB transaction sống trong lúc await socket/result.
- WSS loopback integration test xanh; chưa tuyên bố production TLS/VPS.
- CI matrix, build, contract snapshots, legacy regression và `git diff --check` đều xanh.
- Có `phase3-durable-gateway-evidence.md` ghi rõ kết quả thật và phần chưa kiểm chứng.
- Không sửa launcher OAuth production, không deploy và không yêu cầu AutoCAD thật.

## 14. Rollback và tiêu chí NO-GO

Rollback Phase 3:

- Dừng `phase3_poc` và quay profile về `local`; legacy vẫn là mặc định ngoài Gateway.
- DB POC là file riêng, không tự migrate hoặc xóa dữ liệu legacy; giữ backup để điều tra.
- Revert package protocol, durable modules, simulator và contract `1.1` theo cùng một change set.

Đánh dấu `NO-GO` và không mở Phase 4 nếu có một trong các lỗi:

- Cần giữ DB transaction trong lúc chờ socket để tránh race.
- Duplicate/reconnect có thể làm một write-like command chạy lại sau `started`.
- Job của device A có thể tới Agent B hoặc owner filter đọc chéo ID.
- Restart làm mất terminal result/event hoặc tự đổi `outcome_unknown` thành retry.
- Cần import FastMCP vào state machine/repository/protocol package.
- Contract phải phá nghĩa ba tool Phase 2 thay vì thay đổi additive đã duyệt.
- Chỉ có test HTTP/ASGI giả nhưng không có một kết nối WebSocket/WSS process-level thành công.
