\# Kế hoạch triển khai Phase 4 — Real Windows Agent C1 và UI Lab



\## Tóm tắt



Phase 4 sẽ được triển khai trên nền nhánh `agent/phase3-durable-gateway` sau khi nhánh này được hợp nhất và kiểm thử lại. Mục tiêu là chứng minh luồng thật:



`ChatGPT Web / MCP client → cad.kythuatvang.com → VPS Gateway → outbound WSS → Windows Agent → File IPC → AutoCAD → AutoLISP chỉ đọc`



Phạm vi bị khóa:



\- Một người dùng, một máy Windows được allowlist.

\- Chỉ hỗ trợ thao tác đọc `drawing-info`; không ghi bản vẽ, không chạy AutoLISP tùy ý.

\- Có ứng dụng Agent lab bằng PySide6, cửa sổ tray và hard pause.

\- Tái sử dụng Auth0 hiện tại và host `cad.kythuatvang.com`.

\- Sản phẩm bàn giao là thư mục standalone; chưa làm installer, auto-update, production pairing hay Portal.

\- Phase 4 chỉ đạt khi cả ChatGPT Web và MCP protocol client đều đọc thành công cùng bản vẽ AutoCAD thật.



\## Thay đổi triển khai



\### 1. Chốt nền Phase 3 và hợp đồng Phase 4



\- Hợp nhất `agent/phase3-durable-gateway` vào nhánh triển khai trước khi viết Agent thật; không tái tạo lại Gateway, WSS, SQLite job store hoặc contract Phase 3.

\- Chạy lại toàn bộ Gateway, contract, simulator, legacy tests và build. Mọi regression của Phase 3 là điều kiện dừng.

\- Nâng public contract theo hướng cộng thêm từ `cad.mcp/1.1` lên `cad.mcp/1.2`, nhưng giữ nguyên đúng:

&#x20; - 4 tools: `cad\_list\_devices`, `cad\_observe`, `cad\_query`, `cad\_get\_job`.

&#x20; - 5 resources và 2 prompts hiện có.

&#x20; - Không bổ sung tool ghi.

\- Giữ Agent protocol là `cad.agent/1`, bổ sung các trường có kiểu và giới hạn rõ ràng cho:

&#x20; - Phiên bản Agent, trạng thái AutoCAD, tài liệu đang mở.

&#x20; - Capability/package manifest và hash.

&#x20; - Kết quả quan sát dạng `summary`.

&#x20; - Trạng thái pause và command đang chạy.

\- Bổ sung Gateway migration `0002` để lưu phiên bản Agent, capability/package manifest, trạng thái runtime và tên bản vẽ; tuyệt đối không lưu đường dẫn đầy đủ của file DWG.



\### 2. Windows Agent và thực thi AutoCAD chỉ đọc



Tạo package độc lập tại `apps/desktop\_agent/`, dùng Python 3.12 x64 và pin `PySide6==6.11.1`.



Agent gồm các phần tách biệt:



\- `AgentCore`: quản lý WSS, heartbeat, reconnect, hard pause và hàng đợi command.

\- AutoCAD adapter: tái sử dụng `SafeFileIPCBackend` với `allow\_execute\_lisp=False`.

\- SQLite ledger cục bộ: lưu command trước khi ACK và lưu kết quả terminal trước khi gửi về Gateway.

\- Package verifier: kiểm tra ID, phiên bản và SHA-256 của AutoLISP trước khi chạy.

\- Credential store: lưu lab token bằng Windows DPAPI theo tài khoản Windows hiện tại; không ghi token ra config hoặc log.

\- Diagnostics exporter: tạo gói hỗ trợ đã loại bỏ dữ liệu nhạy cảm.



Command router chỉ chấp nhận chính xác package đọc:



\- Package ID: `autocad.lisp.drawing\_info`.

\- Effect: `read`.

\- Observation level: `summary`.

\- Từ chối trước khi chạm File IPC đối với `detail`, preview image, `write\_fixture`, raw LISP hoặc command không có trong manifest.

\- `cad\_query` trên snapshot summary trả trang rỗng với `total=0`; yêu cầu lấy detail trả `capability\_missing`, không giả lập dữ liệu entity.



Ledger cục bộ lưu `command\_id`, `job\_id`, idempotency key, payload hash, trạng thái, kết quả/lỗi, package version/hash và timestamp:



\- Cùng ID và cùng payload hash: trả lại kết quả đã lưu.

\- Cùng ID nhưng payload khác: trả `replay\_payload\_mismatch`.

\- Read command bị ngắt giữa chừng sau khi Agent khởi động lại được đánh dấu retryable và có thể chạy lại an toàn.

\- Không có write command nào được ghi nhận hoặc thực thi.



AutoLISP dispatcher được nâng phiên bản từ `3.2` lên `3.3-c1` và bổ sung:



\- Tên file DWG dạng basename, không có full path.

\- `entity\_count`, danh sách layer, `layer\_count` và cờ `truncated`.

\- Dispatcher/package version.

\- Escape chuỗi đúng định dạng và giới hạn tối đa 256 layer, 255 ký tự mỗi tên layer.



`document\_revision` được tạo từ SHA-256 của identity cục bộ cộng với summary đã chuẩn hóa; identity chứa đường dẫn chỉ tồn tại nội bộ Agent.



\### 3. UI Agent Lab



UI dùng PySide6 Widgets. Qt main thread chỉ vẽ giao diện; `AgentCore` chạy trong background thread riêng, có asyncio loop và COM initialization riêng. Hai phía giao tiếp bằng queue thread-safe và Qt queued signals. Không dùng WSS, File IPC hoặc COM trực tiếp trong widget.



Màn hình chính dùng tiếng Việt, chỉ hiển thị:



\- Thiết bị.

\- Kết nối máy chủ.

\- Trạng thái AutoCAD.

\- Tên bản vẽ đang mở.

\- Tác vụ hiện tại.

\- Phiên bản Agent/package.

\- `Thử lại`, `Tạm dừng`, `Tiếp tục`, `Chẩn đoán`, `Trợ giúp`.



Không đưa vào Phase 4:



\- Nút cho phép ChatGPT chỉnh sửa.

\- Chế độ rủi ro.

\- Pairing/login.

\- Portal, installer hoặc auto-update.



Thứ tự ưu tiên trạng thái UI:



`paused → incompatible/package mismatch → gateway offline/connecting → AutoCAD closed → no document → modal/busy → remote job running → ready`



Hành vi bắt buộc:



\- Hard pause chặn mọi command mới, kể cả command đọc; heartbeat và chẩn đoán cục bộ vẫn hoạt động.

\- Hard pause không tự gửi `ESC` hoặc can thiệp vào thao tác AutoCAD của người dùng.

\- Đóng cửa sổ chỉ thu nhỏ xuống tray.

\- Tray có trạng thái server/AutoCAD, mở Agent, pause/resume, diagnostics và Exit.

\- Nếu Exit khi có command đang chạy, UI hỏi xác nhận; nếu đồng ý thì kết thúc command bằng lỗi có cấu trúc trước khi thoát.

\- Manual Retry hủy backoff hiện tại và kết nối lại ngay.



Gói diagnostics chỉ chứa phiên bản Agent/Windows/AutoCAD, device ID rút gọn, manifest hash, heartbeat/job/correlation ID, lỗi, timestamp và báo cáo redaction. Nó không được chứa token, private key, full path, nội dung bản vẽ, ảnh chụp màn hình hay toàn bộ chương trình AutoLISP.



Dùng `pyside6-deploy`/Nuitka để tạo bản standalone. Bản onefile chỉ được đo thời gian khởi động, RAM và kích thước để làm bằng chứng, không phải artifact bàn giao. Trước khi phát hành rộng hơn lab phải kiểm tra nghĩa vụ LGPL/commercial của Qt. \[PySide6](https://pypi.org/project/PySide6/), \[pyside6-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html), \[Qt licensing](https://doc.qt.io/qt-6/licensing.html).



\### 4. Gateway, Auth0 và triển khai VPS C1



Thêm profile `phase4\_c1` và feature flag `AUTOCAD\_MCP\_REAL\_AGENT\_C1=1`. Profile phải fail-fast nếu thiếu:



\- SQLite path cố định.

\- Auth0 issuer, audience và JWKS URI.

\- Public origin/allowed host.

\- Một lab device và credential allowlist.

\- Write-disabled policy.



Xác thực:



\- Dùng FastMCP `RemoteAuthProvider` và `JWTVerifier`.

\- Kiểm tra RS256 signature, issuer, audience, expiry, subject và scope `autocad.read` trên từng request.

\- Adapter Auth0 hợp nhất claim từ `scope`, `scp` và `permissions`.

\- `sub` đã xác thực phải ánh xạ đúng lab device.

\- Token sai hoặc thiếu scope trả 401/challenge và không được tạo job, command hay DB row.

\- Gateway tiếp tục từ chối write kể cả khi token vô tình có `autocad.write`.

\- Tool metadata và runtime challenge phải khai báo security scheme tương thích ChatGPT OAuth. \[OpenAI MCP authentication](https://developers.openai.com/apps-sdk/build/auth).



Public API bổ sung:



| Thành phần | Thay đổi Phase 4 |

|---|---|

| `DeviceInfo` | Thêm tùy chọn `runtime\_state`, `document\_name`, `last\_seen\_at`, capabilities |

| `cad\_observe` | Trả summary drawing và `execution\_evidence` |

| `cad\_get\_job` | Trả `agent\_version`, `command\_id`, package ID/version/SHA-256 |

| `cad\_query` | Summary snapshot trả trang entity rỗng; detail bị từ chối rõ ràng |



VPS chạy một Gateway worker với SQLite persistent volume, bind loopback. Cloudflare Tunnel kết thúc TLS/WSS và chuyển `/mcp` cùng `/agent/ws` tại `cad.kythuatvang.com`.



Cutover:



1\. Dựng Gateway VPS và kiểm tra nội bộ trước.

2\. Kết nối Agent lab bằng outbound WSS.

3\. Dừng connector cũ và chuyển hostname sang VPS connector, tránh hai Gateway cùng nhận traffic.

4\. Kiểm tra `/readyz` 200, protected-resource metadata 200, `/mcp` không token trả 401 và Agent hiện online.

5\. Refresh metadata của app trong ChatGPT, cấp token mới rồi chạy E2E trong cuộc trò chuyện mới. \[Connect an MCP server to ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt).



Rollback giữ nguyên backend/config cũ: dừng Agent, thu hồi lab credential, chuyển Cloudflare connector về dịch vụ hiện tại và khởi động lại launcher Phase 4 OAuth cũ. Không xóa SQLite, config hay artifact bằng chứng.



\### 5. Phân phối AutoLISP cho máy lab



\- Artifact standalone chứa manifest và bản `mcp\_dispatch.lsp` đã version hóa.

\- Script lab chỉ sao chép LISP vào thư mục cố định dưới `%LOCALAPPDATA%\\Kythuatvang\\AutoCADAgent\\packages\\...`.

\- Hướng dẫn người vận hành tự thêm thư mục này vào AutoCAD Support File Search Path/TRUSTEDPATHS.

\- Script không tự sửa AutoCAD profile và không dùng đường dẫn máy phát triển.

\- Khi khởi động, Agent so khớp hash file, phiên bản manifest và phiên bản dispatcher do LISP báo về. Sai bất kỳ giá trị nào sẽ chuyển UI sang `incompatible`, tắt capability và không chạy command.



\## Kế hoạch kiểm thử và nghiệm thu



\### Tự động



\- Regression đầy đủ cho Phase 3, legacy File IPC và contract snapshots.

\- Contract tests cho `cad.mcp/1.2`, Hello, Heartbeat, ObservationResult, giới hạn payload và từ chối extra fields.

\- Gateway tests:

&#x20; - Profile fail-closed.

&#x20; - Ma trận token issuer/audience/expiry/scope.

&#x20; - Đúng một lab device.

&#x20; - Không có write/raw LISP.

&#x20; - SQLite migration/restart.

&#x20; - Duplicate, stale session, reconnect và manifest mismatch.

&#x20; - Entity count lấy từ drawing summary, không lấy từ mảng `entities` rỗng.

\- Agent headless tests:

&#x20; - Ledger crash/replay và payload mismatch.

&#x20; - WSS reconnect/backoff/manual Retry.

&#x20; - Hard pause được duy trì qua restart.

&#x20; - Mapping toàn bộ lỗi File IPC.

&#x20; - Command bị cấm không được gọi backend.

\- UI tests bằng `pytest-qt` ở chế độ offscreen:

&#x20; - Mapping state sang nội dung/nút.

&#x20; - Widget chỉ gửi intent.

&#x20; - Không block UI thread.

&#x20; - Keyboard/focus.

&#x20; - Close-to-tray và Exit khi đang chạy job.

&#x20; - Diagnostics redaction.

\- CI Windows Python 3.12:

&#x20; - Unit/UI/build.

&#x20; - Tạo standalone artifact.

&#x20; - Xác nhận schema snapshots không trôi ngoài thay đổi đã duyệt.



\### Thử nghiệm AutoCAD thật



Chạy trên máy Windows lab với bản vẽ không nhạy cảm:



\- Agent start/stop.

\- AutoCAD đóng.

\- Không có document.

\- Document hợp lệ.

\- AutoCAD đang chạy command thủ công.

\- Modal dialog.

\- Đổi document giữa lúc đọc.

\- Dispatcher thiếu.

\- Package hash sai.

\- Mất mạng rồi reconnect.

\- Gửi trùng cùng command.

\- Pause, gọi từ xa bị `paused\_by\_user`, resume rồi gọi lại thành công.



Kiểm tra UI ở DPI 100%, 150%, 200% và nhiều màn hình; chụp bằng chứng cho trạng thái ready, busy/modal, paused và diagnostics đã redaction. Chạy standalone trên máy phát triển và một Windows 11 VM sạch; ghi lại startup time, RAM, package size và kết quả Windows Defender/SmartScreen.



\### E2E bắt buộc



Thực hiện cùng một yêu cầu bằng:



1\. MCP protocol client thật.

2\. ChatGPT Web sau khi chọn app AutoCAD trong cuộc trò chuyện mới.



Prompt nghiệm thu:



> Hãy đọc bản vẽ AutoCAD đang mở và cho biết tên bản vẽ, số entity và danh sách layer.



Kết quả phải:



\- Đúng máy AutoCAD allowlist và đúng document đang mở.

\- Có job ID, command ID, Agent version, package ID/version/SHA-256.

\- Không chứa full path, token hoặc nội dung bản vẽ ngoài summary cho phép.

\- Giữ được lỗi có cấu trúc cho busy, modal, document switch và package mismatch.

\- Chứng minh máy người dùng không mở inbound listener hoặc tunnel.

\- Ghi lại độ trễ của 10 lần gọi nhỏ; Phase 4 chỉ thu thập số liệu, chưa đặt production SLO.



\## Điều kiện NO-GO và bằng chứng bàn giao



Không nghiệm thu nếu xảy ra một trong các trường hợp:



\- Phase 3 chưa hợp nhất hoặc regression chưa xanh.

\- Có đường gọi write/raw LISP.

\- Token, full path hay nội dung nhạy cảm xuất hiện trong UI, log, DB hoặc diagnostics.

\- Gateway định tuyến nhầm thiết bị.

\- UI gọi COM/File IPC trực tiếp hoặc bị treo khi mất mạng/AutoCAD bận.

\- Package mismatch vẫn cho phép chạy.

\- Chỉ kiểm tra metadata/JWKS mà chưa thực hiện read thật bằng token mới.

\- Không bảo toàn structured error hoặc không truy ra package hash của kết quả.

\- Cutover public host chưa có rollback đã thử nghiệm.



Artifact nghiệm thu gồm standalone Agent, SHA-256 artifact/package, manifest, hướng dẫn lab provisioning, ảnh UI, kết quả kiểm thử, thông số đóng gói, correlation IDs của hai luồng E2E, xác nhận không có inbound port và tài liệu giới hạn C1.



\## Giả định đã khóa



\- Phase 4 bắt đầu từ Phase 3 hiện có, không từ `main` cũ.

\- Public host là `cad.kythuatvang.com`.

\- Auth0 hiện tại được tái sử dụng; phải cấp token mới có `autocad.read`.

\- Một VPS Gateway worker, một lab user và một lab device.

\- Python 3.12 x64, PySide6 6.11.1, standalone folder.

\- Credential C1 là lab credential bảo vệ bằng DPAPI; production pairing/device key thuộc Phase 5.

\- Không deploy installer, auto-update, Portal, write approval hoặc production multi-user trong Phase 4.



