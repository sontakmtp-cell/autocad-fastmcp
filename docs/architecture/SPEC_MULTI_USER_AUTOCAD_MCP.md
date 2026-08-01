# ĐẶC TẢ SẢN PHẨM
## AutoCAD MCP nhiều người dùng với Central Gateway và Desktop Agent

**Mã tài liệu:** PRD-AUTOCAD-MCP-MULTIUSER-001  
**Trạng thái:** Bản đặc tả sản phẩm đề xuất  
**Đối tượng sử dụng tài liệu:** Chủ dự án, Product Manager, UX/UI, kiến trúc sư hệ thống, Codex/AI coding agents, đội phát triển và kiểm thử  
**Ngôn ngữ sản phẩm mặc định:** Tiếng Việt; kiến trúc phải sẵn sàng cho đa ngôn ngữ

---

## 1. Mục đích tài liệu

Tài liệu này mô tả **sản phẩm phải hoạt động như thế nào sau khi hoàn thiện**, tập trung vào:

- Người dùng cuối có thể làm gì với AutoCAD qua ChatGPT.
- Trải nghiệm cài đặt và kết nối máy tính với tài khoản.
- Cách chọn đúng máy, đúng AutoCAD và đúng bản vẽ.
- Cách hệ thống quan sát, lập kế hoạch, xem trước, xin duyệt, thực thi, kiểm tra và hoàn tác.
- Các màn hình và trạng thái cần có trong Desktop Agent và cổng quản trị web.
- Cách vận hành hệ thống nhiều người dùng, nhiều thiết bị và nhiều phiên ChatGPT đồng thời.
- Quyền hạn, bảo mật, audit, xử lý lỗi và các tiêu chí nghiệm thu.

Đây là **đặc tả chức năng và trải nghiệm sản phẩm**, không thay thế kế hoạch kiến trúc hoặc kế hoạch triển khai kỹ thuật.

### 1.1. Căn cứ thiết kế

Đặc tả này bám theo định hướng kiến trúc hybrid: workflow có sẵn chỉ là đường tắt an toàn; ChatGPT vẫn có thể tạo **CAD Program có cấu trúc**, quan sát preview, sửa kế hoạch và dùng primitive khi cần. fileciteturn10file0

Central Gateway sử dụng FastMCP cho lớp MCP-facing, schema, transport và request lifecycle; còn device routing, job state, CAD Program, AutoCAD runtime, preview và rollback là domain riêng của hệ thống. fileciteturn10file1

---

## 2. Tầm nhìn sản phẩm

Người dùng mở AutoCAD trên máy của họ, mở ChatGPT và nói bằng ngôn ngữ tự nhiên:

> “Kiểm tra bản vẽ này, dọn các đường trùng, chuẩn hóa layer và đánh kích thước cho tấm thép lớn nhất. Cho tao xem trước rồi mới sửa.”

Hệ thống phải:

1. Nhận biết đúng người dùng.
2. Xác định đúng máy tính và phiên AutoCAD thuộc người đó.
3. Quan sát bản vẽ hiện tại.
4. Hiểu mục tiêu và lập kế hoạch.
5. Tạo preview không phá bản vẽ gốc.
6. Trình bày thay đổi bằng ngôn ngữ dễ hiểu và hình ảnh.
7. Chỉ sửa bản vẽ sau khi người dùng duyệt nếu thao tác có rủi ro.
8. Thực thi trong transaction/Undo Group.
9. Kiểm tra kết quả.
10. Cho phép hoàn tác an toàn.

Sản phẩm phải tạo cảm giác như người dùng đang làm việc với một **trợ lý CAD hiểu bản vẽ và biết giữ an toàn**, không phải một bot chạy macro cứng.

---

## 3. Nguyên tắc sản phẩm bắt buộc

### 3.1. Một tài khoản chỉ điều khiển thiết bị được cấp quyền

Người dùng không được nhìn thấy, chọn hoặc điều khiển thiết bị của tài khoản khác, kể cả khi biết `device_id`.

### 3.2. Desktop Agent luôn kết nối outbound

Người dùng không phải:

- mở port;
- cấu hình NAT;
- tạo tunnel riêng;
- tạo subdomain riêng;
- nhập access token thủ công vào `.env`.

### 3.3. ChatGPT quyết định mục tiêu; Agent thi công an toàn

ChatGPT có thể sáng tạo kế hoạch hình học. Gateway và Desktop Agent chịu trách nhiệm kiểm tra quyền, schema, rủi ro, giới hạn, transaction và kết quả.

### 3.4. Ít công cụ công khai nhưng khả năng rộng

ChatGPT chỉ nhìn thấy một nhóm capability cấp cao, ổn định. Primitive AutoCAD chi tiết nằm phía sau CAD Program và runtime nội bộ.

### 3.5. Đọc trước, sửa sau

Mọi tác vụ phải bắt đầu từ trạng thái bản vẽ đủ mới. Tác vụ ghi có rủi ro phải có preview hoặc xác nhận rõ ràng.

### 3.6. Không âm thầm sửa nhầm bản vẽ

Nếu active document hoặc `document_revision` thay đổi từ lúc tạo preview, hệ thống phải chặn commit và yêu cầu tạo preview mới.

### 3.7. Không chạy mã tùy ý từ xa

Không cho phép ChatGPT hoặc người dùng remote chạy tùy ý:

- AutoLISP;
- Python;
- PowerShell;
- shell command;
- .NET code;
- file operation ngoài vùng cho phép.

### 3.8. Mỗi thay đổi phải truy vết được

Mọi lệnh phải có user, device, document, job, thời điểm, kết quả và trạng thái rollback tương ứng.

---

## 4. Phạm vi sản phẩm

### 4.1. Phạm vi chính

Sản phẩm gồm ba bề mặt:

1. **ChatGPT Web + AutoCAD MCP**  
   Nơi người dùng giao việc bằng ngôn ngữ tự nhiên và xem kết quả.

2. **AutoCAD Desktop Agent cho Windows**  
   Chương trình chạy trên máy người dùng, kết nối Gateway và điều khiển AutoCAD cục bộ.

3. **Cổng quản lý web**  
   Nơi người dùng quản lý tài khoản, thiết bị, quyền, lịch sử, kết nối và bảo mật.

Ngoài ra có **Central Gateway** và **hệ thống vận hành quản trị**, nhưng đây chủ yếu là thành phần nền.

### 4.2. AutoCAD được hỗ trợ

MVP ưu tiên môi trường hiện tại của dự án:

- Windows 10/11.
- AutoCAD LT 2024+ và các phiên bản đã được kiểm thử.
- AutoCAD đầy đủ có thể hỗ trợ khi backend tương thích và qua ma trận kiểm thử.

### 4.3. Chế độ tương thích

Sản phẩm phải duy trì:

- **Cloud mode:** ChatGPT → Central Gateway → Desktop Agent → AutoCAD.
- **Local mode:** MCP chạy trực tiếp trên một máy như hệ thống hiện tại, dành cho phát triển, offline hoặc người dùng không muốn Gateway.

---

## 5. Vai trò người dùng

### 5.1. Người dùng cá nhân

- Đăng ký tài khoản.
- Ghép một hoặc nhiều máy.
- Điều khiển AutoCAD trên thiết bị của mình.
- Xem preview và phê duyệt.
- Xem lịch sử và rollback.

### 5.2. Thành viên tổ chức

- Sử dụng thiết bị được tổ chức cấp.
- Có quyền đọc, ghi hoặc xuất file tùy vai trò.
- Không tự thay đổi chính sách cấp tổ chức nếu không có quyền.

### 5.3. Quản trị viên tổ chức

- Mời hoặc khóa thành viên.
- Gán thiết bị, vai trò và giới hạn.
- Xem audit của tổ chức theo chính sách.
- Thu hồi thiết bị hoặc credential.

### 5.4. Quản trị viên hệ thống

- Quản lý trạng thái dịch vụ.
- Hỗ trợ người dùng mà không xem nội dung bản vẽ khi không cần thiết.
- Thu hồi credential, khóa tài khoản vi phạm.
- Xem metrics, lỗi hệ thống và audit bảo mật.

---

## 6. Mô hình trải nghiệm tổng thể

```text
Người dùng mở AutoCAD
        │
Desktop Agent xác nhận AutoCAD sẵn sàng
        │
Người dùng mở ChatGPT và kết nối AutoCAD MCP
        │
ChatGPT xác định tài khoản và thiết bị đang chọn
        │
Observe / Query bản vẽ
        │
Lập workflow hoặc CAD Program
        │
Preview + báo cáo thay đổi
        │
Người dùng duyệt
        │
Commit trên đúng document revision
        │
Validate kết quả
        │
Hoàn tất hoặc rollback
```

### 6.1. Trải nghiệm mặc định

Người dùng không cần biết các khái niệm Gateway, WebSocket, job queue, schema hay primitive. Giao diện chỉ nên nói:

- Máy nào đang được chọn.
- AutoCAD có sẵn sàng hay không.
- Bản vẽ nào đang mở.
- Hệ thống dự định làm gì.
- Có bao nhiêu đối tượng sẽ bị ảnh hưởng.
- Có cần duyệt hay không.
- Kết quả đã hoàn tất hay gặp lỗi.
- Có thể hoàn tác hay không.

---

## 7. Hành trình người dùng

## 7.1. Đăng ký và kết nối lần đầu

### Mục tiêu

Một người không biết lập trình có thể cài và sử dụng trong một quy trình có hướng dẫn.

### Luồng chuẩn

1. Người dùng tạo tài khoản hoặc đăng nhập bằng Auth0.
2. Cổng web hiển thị nút **Tải AutoCAD Desktop Agent**.
3. Người dùng tải file `.exe` hoặc `.msi` có chữ ký số.
4. Trình cài đặt:
   - kiểm tra Windows;
   - kiểm tra phiên bản AutoCAD;
   - cài Desktop Agent;
   - cài hoặc hướng dẫn nạp AutoLISP dispatcher;
   - tạo shortcut và tùy chọn chạy cùng Windows.
5. Agent khởi động và hiển thị **Ghép thiết bị**.
6. Agent mở trình duyệt hoặc hiển thị mã ghép ngắn hạn.
7. Người dùng xác nhận đúng tên máy trong trang web.
8. Gateway cấp credential riêng cho device.
9. Agent lưu credential bằng Windows Credential Manager hoặc DPAPI.
10. Agent kiểm tra AutoCAD, dispatcher và quyền.
11. Trang kết thúc hiển thị:
    - tên thiết bị;
    - trạng thái online;
    - AutoCAD detected;
    - ChatGPT connection instructions.

### Tiêu chí trải nghiệm

- Không yêu cầu copy token.
- Không yêu cầu sửa file `.env`.
- Không yêu cầu chạy PowerShell thủ công.
- Nếu thiếu dispatcher, hệ thống phải hướng dẫn cụ thể theo từng bước.
- Nếu AutoCAD chưa mở, Agent vẫn được ghép và hiển thị “Đang chờ AutoCAD”.

---

## 7.2. Sử dụng hằng ngày

### Luồng chuẩn

1. Agent tự chạy hoặc người dùng mở Agent.
2. Agent kết nối Gateway.
3. Người dùng mở AutoCAD và bản vẽ.
4. Agent cập nhật trạng thái:
   - Online;
   - AutoCAD sẵn sàng;
   - tên document;
   - Read-only hoặc Write enabled.
5. Người dùng mở ChatGPT.
6. ChatGPT MCP đọc danh sách thiết bị của tài khoản.
7. Nếu chỉ có một thiết bị online, chọn tự động.
8. Nếu có nhiều thiết bị, ChatGPT hỏi người dùng chọn hoặc dùng thiết bị mặc định theo cuộc trò chuyện.
9. Người dùng giao việc.
10. ChatGPT quan sát bản vẽ trước khi lập kế hoạch nếu dữ liệu hiện tại chưa đủ.

---

## 7.3. Tác vụ chỉ đọc

Ví dụ:

- “Bản vẽ này có bao nhiêu layer?”
- “Tìm các polyline chưa đóng.”
- “Tóm tắt chi tiết lớn nhất.”
- “Chụp phần góc trên bên phải.”

Luồng:

1. ChatGPT gọi observe/query.
2. Gateway kiểm tra quyền đọc và ownership.
3. Agent đọc trạng thái và trả snapshot hoặc artifact.
4. ChatGPT trả lời, không yêu cầu approval.

Tác vụ chỉ đọc không được thay đổi viewport một cách gây gián đoạn, trừ khi người dùng yêu cầu zoom hoặc screenshot cụ thể.

---

## 7.4. Tác vụ ghi an toàn, phạm vi nhỏ

Ví dụ:

- Tạo một circle với kích thước và vị trí rõ ràng.
- Sửa một text đã được người dùng chọn.
- Đổi thuộc tính layer của một entity cụ thể.

Hệ thống có thể cho phép thực thi ngay khi tất cả điều kiện sau đúng:

- người dùng có quyền ghi;
- mục tiêu được xác định rõ;
- số entity bị ảnh hưởng dưới ngưỡng;
- thao tác có thể undo;
- không xóa dữ liệu;
- document revision còn mới;
- chính sách người dùng cho phép “thực thi nhanh”.

Trước khi chạy, ChatGPT vẫn phải thông báo ngắn gọn về hành động sắp thực hiện.

---

## 7.5. Tác vụ có preview và phê duyệt

Bắt buộc preview đối với:

- xóa entity;
- sửa hàng loạt;
- thay đổi geometry của nhiều đối tượng;
- mở hoặc lưu sang đường dẫn mới;
- dọn bản vẽ;
- tự động dimension phạm vi lớn;
- chạy CAD Program nhiều bước;
- tác vụ vượt ngưỡng rủi ro;
- tác vụ do primitive fallback tạo ra.

### Preview phải hiển thị

- Tên máy và document đích.
- `document_revision` dùng để tạo preview.
- Mục tiêu của tác vụ.
- Số entity sẽ tạo, sửa và xóa.
- Layer, block, dimension hoặc file bị ảnh hưởng.
- Screenshot hoặc ảnh đánh dấu vùng thay đổi khi có thể.
- Cảnh báo và giả định.
- Nút hoặc hành động: **Duyệt**, **Sửa yêu cầu**, **Hủy**.

### Quy tắc commit

- Approval chỉ hợp lệ cho đúng user, device, document, program revision và preview revision.
- Approval có thời hạn.
- Nếu AutoCAD hoặc bản vẽ thay đổi, approval hết hiệu lực.
- Commit phải chạy trong một transaction/Undo Group khi backend hỗ trợ.

---

## 7.6. Tác vụ bằng workflow có sẵn

Workflow dùng cho công việc phổ biến:

- auto-dimension;
- kiểm tra và sửa dimension;
- dọn đường trùng;
- chuẩn hóa layer;
- kiểm tra bản vẽ;
- xuất PDF/DXF;
- P&ID workflow được hỗ trợ.

ChatGPT có thể chọn workflow khi phù hợp, nhưng phải cho phép chuyển sang CAD Program nếu yêu cầu vượt khả năng workflow.

Trong giao diện, người dùng không cần biết tên kỹ thuật của workflow. ChatGPT diễn đạt theo mục tiêu, ví dụ:

> “Tao sẽ dùng quy trình đánh kích thước cơ khí, phát hiện chi tiết, tạo phương án và cho mày xem trước.”

---

## 7.7. Tác vụ bằng CAD Program

CAD Program được dùng khi:

- yêu cầu gồm nhiều bước;
- hình học mới hoặc ít gặp;
- cần biến, pattern hoặc tham chiếu kết quả bước trước;
- không có workflow phù hợp;
- người dùng yêu cầu tạo một cụm chi tiết hoàn chỉnh.

Người dùng không bắt buộc nhìn thấy JSON/DSL. Mặc định ChatGPT trình bày:

- kế hoạch bằng ngôn ngữ tự nhiên;
- các tham số chính;
- preview;
- cảnh báo;
- kết quả kiểm tra.

Người dùng nâng cao có thể bật chế độ xem CAD Program chi tiết.

---

## 7.8. Một tài khoản có nhiều thiết bị

### Hành vi mặc định

- ChatGPT nhớ thiết bị đã chọn trong phạm vi cuộc trò chuyện.
- Mỗi cuộc trò chuyện có thể chọn một thiết bị khác.
- Không dùng một “selected device” toàn cục duy nhất cho mọi cuộc trò chuyện.

### Khi bắt đầu tác vụ

ChatGPT phải xác nhận lại thiết bị khi:

- có nhiều thiết bị online và chưa chọn;
- thiết bị cũ offline;
- người dùng nhắc tên máy khác;
- tác vụ ghi có rủi ro cao;
- active document khác đáng kể với lần quan sát trước.

### Ví dụ phản hồi

> “Máy đang chọn: **PC Xưởng** — AutoCAD sẵn sàng — `khung-may.dwg`. Tao sẽ làm trên máy này.”

---

## 7.9. Mất mạng và reconnect

### Khi Agent mất kết nối trước khi nhận lệnh

- Job chuyển sang `device_offline` hoặc chờ trong khoảng thời gian chính sách cho phép.
- ChatGPT báo rõ không có thay đổi nào được thực hiện.

### Khi mất kết nối sau khi Agent nhận lệnh

Gateway phải phân biệt:

- chưa chạy;
- đang chạy;
- đã chạy nhưng chưa nhận kết quả;
- trạng thái chưa xác định.

Không tự gửi lại lệnh ghi khi chưa đối soát idempotency.

Sau reconnect, Agent gửi:

- job cuối cùng đã nhận;
- job đang chạy;
- kết quả đã hoàn tất nhưng chưa đồng bộ;
- active document và revision hiện tại.

ChatGPT phải nói rõ khi trạng thái chưa chắc chắn và không được tuyên bố thành công nếu chưa có bằng chứng.

---

## 7.10. Hoàn tác và khôi phục

Sau mỗi tác vụ ghi thành công, ChatGPT hiển thị:

- mã thao tác;
- thời điểm;
- số entity thay đổi;
- trạng thái có thể rollback;
- thời hạn hoặc điều kiện rollback.

Rollback phải kiểm tra:

- đúng document;
- đúng checkpoint;
- document chưa có thay đổi xung đột;
- người dùng có quyền;
- Agent còn online.

Nếu rollback trực tiếp không an toàn, hệ thống phải đề xuất mở Undo trong AutoCAD hoặc tạo phương án đảo ngược có preview.

---

## 8. Đặc tả giao diện ChatGPT MCP

## 8.1. Nhóm capability công khai

Tên cuối cùng có thể thay đổi, nhưng sản phẩm phải có các khả năng tương đương:

### `cad.capabilities`

Mục đích:

- xem thiết bị;
- phiên bản Agent;
- trạng thái AutoCAD;
- backend và operation hỗ trợ;
- giới hạn hiện tại;
- chế độ read-only/write.

### `cad.observe`

Mục đích:

- đọc document hiện tại;
- viewport;
- selection;
- entity summary;
- screenshot;
- snapshot ID và document revision.

### `cad.query`

Mục đích:

- tìm entity theo loại, layer, vùng, kích thước và quan hệ;
- trả kết quả có phân trang hoặc artifact reference;
- không bắt ChatGPT đọc toàn bộ drawing khi không cần.

### `cad.prepare`

Mục đích:

- tạo workflow plan hoặc CAD Program;
- kiểm tra schema và capability;
- trả risk report;
- chưa sửa bản vẽ.

### `cad.preview`

Mục đích:

- tạo ảnh xem trước;
- entity diff;
- validation sơ bộ;
- preview ID và approval requirement.

### `cad.execute`

Mục đích:

- thực thi plan đã được phép;
- tạo job;
- theo dõi trạng thái;
- không cho phép thay đổi nội dung plan sau approval.

### `cad.job`

Mục đích:

- xem trạng thái;
- progress;
- cancel;
- kết quả;
- error detail dễ hiểu.

### `cad.validate`

Mục đích:

- kiểm tra kết quả hình học, dimension, layer hoặc mục tiêu cụ thể;
- so sánh trước và sau.

### `cad.rollback`

Mục đích:

- hoàn tác transaction hợp lệ;
- trả kết quả và revision mới.

### `cad.devices`

Mục đích:

- liệt kê;
- chọn;
- xem trạng thái;
- đổi tên hiển thị;
- không cho truy cập thiết bị ngoài ownership.

## 8.2. Cách ChatGPT giao tiếp với người dùng

ChatGPT phải ưu tiên câu dễ hiểu:

- “AutoCAD đang bận với lệnh PLINE.”
- “Bản vẽ đã thay đổi sau khi tạo preview.”
- “Tao chưa sửa gì.”
- “Đã tạo 12 kích thước trong một nhóm Undo.”

Không hiển thị dump JSON dài, stack trace hoặc lỗi nội bộ trừ khi người dùng bật chế độ kỹ thuật.

## 8.3. Xác nhận ngữ cảnh trước tác vụ ghi

Trước tác vụ ghi có rủi ro, ChatGPT phải nêu:

- máy;
- document;
- phạm vi;
- thay đổi dự kiến;
- có preview hay không.

## 8.4. Progress

Với job dài, ChatGPT có thể hiển thị các giai đoạn:

```text
Đang quan sát bản vẽ
Đang lập kế hoạch
Đang tạo preview
Đang chờ phê duyệt
Đang thực thi
Đang kiểm tra kết quả
Hoàn tất
```

Không được tạo cảm giác “đang chạy” khi Agent thực tế đã offline.

---

## 9. Đặc tả Desktop Agent

## 9.1. Hình thức ứng dụng

MVP: ứng dụng Windows có system tray, chạy trong user session.  
Lâu dài: có thể tách background service và tray UI nếu cần, nhưng thao tác AutoCAD phải phù hợp với session desktop của người dùng.

## 9.2. Màn hình trạng thái chính

Phải hiển thị:

- Tài khoản đã liên kết.
- Tên thiết bị.
- Device ID rút gọn.
- Gateway: Connected/Connecting/Offline.
- AutoCAD: Ready/Busy/Closed/Not found.
- Active document.
- Dispatcher: Ready/Missing/Outdated.
- Chế độ: Read-only/Write enabled/Paused.
- Agent version.
- Last sync.

## 9.3. Điều khiển bắt buộc

- Kết nối lại.
- Tạm dừng điều khiển từ xa.
- Chuyển read-only.
- Cho phép ghi.
- Mở AutoCAD hoặc hướng dẫn mở.
- Kiểm tra dispatcher.
- Xem tác vụ hiện tại.
- Hủy tác vụ nếu có thể.
- Mở lịch sử gần đây.
- Ngắt liên kết thiết bị.
- Kiểm tra cập nhật.
- Gửi gói chẩn đoán đã loại bỏ secret.

## 9.4. Thông báo trên máy

Agent phải thông báo khi:

- thiết bị vừa được ghép;
- có tác vụ ghi bắt đầu;
- tác vụ ghi hoàn tất;
- tác vụ bị từ chối;
- Agent chuyển read-only;
- credential bị thu hồi;
- update sẵn sàng;
- AutoCAD hoặc dispatcher gặp lỗi.

Thông báo không được chứa nội dung nhạy cảm quá mức hoặc đường dẫn đầy đủ nếu người dùng tắt hiển thị chi tiết.

## 9.5. Emergency disconnect

Một nút **Tạm dừng điều khiển từ xa** phải:

- có hiệu lực ngay;
- từ chối command mới;
- cố gắng hủy command chưa commit;
- không tự bật lại sau restart nếu người dùng đã chọn pause thủ công;
- hiển thị rõ trong ChatGPT rằng thiết bị đang bị người dùng khóa.

## 9.6. Cài đặt dispatcher

Agent phải:

- phát hiện dispatcher thiếu hoặc cũ;
- giải thích lý do;
- tự động cài khi có thể và được người dùng đồng ý;
- không tắt `SECURELOAD`;
- không tự thêm path thiếu an toàn;
- kiểm tra dispatcher trên từng document mới mở.

## 9.7. Cập nhật Agent

- Package phải có chữ ký.
- Kiểm tra integrity trước cài.
- Không update giữa một transaction.
- Cho phép trì hoãn trong giới hạn chính sách.
- Có rollback phiên bản khi update thất bại.
- Gateway phải cảnh báo Agent quá cũ hoặc không tương thích.

---

## 10. Đặc tả cổng quản lý web

## 10.1. Dashboard người dùng

Hiển thị:

- Số thiết bị đã đăng ký.
- Thiết bị online/offline.
- AutoCAD ready/busy.
- Tác vụ gần đây.
- Cảnh báo bảo mật hoặc update.
- Hướng dẫn kết nối ChatGPT.

## 10.2. Quản lý thiết bị

Người dùng có thể:

- xem tên và trạng thái;
- đổi tên;
- đặt thiết bị mặc định;
- chuyển read-only mặc định;
- xem Agent version;
- xem lần online cuối;
- thu hồi credential;
- xóa hoặc re-enroll;
- giới hạn loại operation;
- xem audit của thiết bị.

## 10.3. Ghép thiết bị

Trang enrollment phải hiển thị:

- mã ghép;
- tên máy do Agent báo;
- vị trí gần đúng nếu chính sách cho phép;
- thời gian hết hạn;
- cảnh báo không xác nhận máy lạ.

## 10.4. Lịch sử tác vụ

Mỗi bản ghi gồm:

- thời gian;
- user;
- device;
- document fingerprint hoặc tên rút gọn;
- loại tác vụ;
- read/write;
- trạng thái;
- số entity tạo/sửa/xóa;
- duration;
- approval;
- rollback availability;
- error code nếu có.

Không lưu token, secret, nội dung file hoặc screenshot vô thời hạn theo mặc định.

## 10.5. Quyền và vai trò

Đối với tổ chức, cổng web phải hỗ trợ:

- Owner;
- Admin;
- Operator;
- Reviewer;
- Read-only.

Có thể gán theo user, device hoặc nhóm thiết bị.

## 10.6. Security page

- Phiên đăng nhập.
- Thiết bị đã liên kết.
- Credential đã thu hồi.
- Lịch sử đăng nhập quan trọng.
- Nút đăng xuất tất cả.
- Xác thực đa yếu tố nếu Auth0 tenant bật.

---

## 11. Danh mục tính năng sản phẩm

## 11.1. Quản lý tài khoản và thiết bị

**Bắt buộc:**

- Đăng nhập OAuth/OIDC.
- User ID ổn định dựa trên `sub`.
- Một user có nhiều device.
- Device enrollment ngắn hạn.
- Device credential riêng.
- Revoke và re-enroll.
- Device ownership validation trên mọi request.

## 11.2. Quan sát bản vẽ

**Bắt buộc:**

- Active document.
- Document identity và revision.
- Backend/capability.
- Entity count theo nhóm.
- Layer/block summary.
- Selection hiện tại.
- Viewport và screenshot.
- Snapshot có thời điểm và revision.

**Nâng cao:**

- Scene graph.
- Spatial index.
- Quan hệ inside/intersect/touch/parallel/perpendicular/concentric/aligned.
- Feature inference như part, hole, slot, centerline.

## 11.3. Query thông minh

- Lọc theo type, layer, màu, bounds và thuộc tính.
- Query theo region.
- Query theo selection hiện tại.
- Query theo quan hệ hình học.
- Pagination.
- Stable entity references trong phạm vi snapshot.

## 11.4. Workflow an toàn

- Auto-dimension.
- Dimension audit/repair.
- Duplicate cleanup.
- Layer standardization.
- Drawing audit.
- Export PDF/DXF.
- Các workflow chuyên ngành được đóng gói thành skill.

Skill phải có mô tả, input, capability, validation, risk và recovery; không phải public tool riêng cho từng skill.

## 11.5. CAD Program

### CAD Program v0

- Danh sách primitive tuyến tính.
- Biến cơ bản.
- Tham chiếu kết quả bước trước.
- Create/move/copy/rotate/scale/erase có allowlist.
- Layer và annotation cơ bản.
- Execution budget.
- Preview và transaction.

### CAD Program v1

- Expression.
- Loop/pattern.
- Selection query.
- Conditional có giới hạn.
- Group/block.
- Patch program.

### CAD Program v2

- Constraint và quan hệ hình học nâng cao.
- Reusable component.
- Layout/plotting nâng cao.
- Scene graph aware planning.
- Validation rule tùy skill.

## 11.6. Preview và diff

Preview phải hỗ trợ tối thiểu:

- entity sẽ tạo;
- entity sẽ sửa;
- entity sẽ xóa;
- bounding region;
- screenshot before/after hoặc overlay;
- risk summary;
- validation warning;
- revision binding.

## 11.7. Job management

- Tạo job ID.
- Queue theo device.
- Một write job tại một thời điểm trên một device.
- Read jobs có thể song song nếu an toàn.
- Progress state.
- Cancel.
- Deadline.
- Retry có kiểm soát.
- Reconnect reconciliation.
- Result retention.

## 11.8. Validation

- Kiểm tra operation thực thi đủ.
- So sánh entity diff với plan.
- Kiểm tra geometry cơ bản.
- Kiểm tra layer/style/dimension theo rule.
- Không đánh dấu thành công nếu validation bắt buộc thất bại.

## 11.9. Rollback

- Rollback transaction gần nhất khi hợp lệ.
- Lưu checkpoint metadata.
- Phát hiện xung đột revision.
- Audit rollback như một job riêng.

---

## 12. Quyền, rủi ro và approval

## 12.1. Scope đề xuất

- `autocad.read`
- `autocad.entity.create`
- `autocad.entity.modify`
- `autocad.entity.delete`
- `autocad.annotation.write`
- `autocad.file.open`
- `autocad.file.save`
- `autocad.file.export`
- `autocad.device.manage`
- `autocad.admin`

Trong migration có thể giữ `autocad.write` làm scope tổng hợp, nhưng Gateway phải ánh xạ sang operation chi tiết.

## 12.2. Mức rủi ro

### Low

- Query/read.
- Screenshot.
- Tạo ít entity mới, không ảnh hưởng entity cũ.

### Medium

- Sửa thuộc tính.
- Di chuyển/copy số lượng nhỏ.
- Dimension trong vùng giới hạn.

### High

- Xóa.
- Sửa hàng loạt.
- Mở/lưu file.
- CAD Program phức tạp.
- Thao tác vượt budget.

### Blocked

- Arbitrary code.
- Path ngoài allowlist.
- Device không thuộc user.
- Token thiếu quyền.
- Program schema không hỗ trợ.
- Document revision stale.

## 12.3. Chính sách approval

- Low: có thể chạy ngay theo cài đặt user.
- Medium: preview mặc định, có thể cho phép “nhớ lựa chọn” theo loại operation.
- High: luôn preview và duyệt rõ ràng.
- Blocked: không có cách bypass từ ChatGPT.

---

## 13. Trạng thái thiết bị và AutoCAD

### Device connection

- `enrolling`
- `online`
- `offline`
- `paused`
- `revoked`
- `update_required`
- `incompatible`

### AutoCAD state

- `not_detected`
- `starting`
- `ready`
- `busy`
- `modal_blocked`
- `document_missing`
- `dispatcher_missing`
- `dispatcher_outdated`
- `error`

### Document state

- `open`
- `modified`
- `read_only`
- `closing`
- `changed_since_preview`
- `unsupported`

ChatGPT và Agent phải dùng thông điệp thân thiện tương ứng, không chỉ hiển thị mã trạng thái.

---

## 14. Vòng đời job

```text
created
→ validated
→ queued
→ delivered
→ acknowledged
→ running
→ validating_result
→ completed
```

Các trạng thái khác:

- `awaiting_approval`
- `rejected`
- `cancel_requested`
- `cancelled`
- `failed`
- `timed_out`
- `device_offline`
- `autocad_busy`
- `dispatcher_missing`
- `revision_conflict`
- `result_unknown`
- `rolled_back`

### Quy tắc

- Trạng thái phải bền qua restart Gateway.
- Agent phải lưu tối thiểu ledger chống duplicate gần đây.
- `completed` chỉ được ghi khi có result envelope hợp lệ.
- `result_unknown` không được tự chuyển thành `completed` nếu chưa reconcile.
- Một job ghi không được chạy đồng thời với job ghi khác trên cùng document/device.

---

## 15. Xử lý lỗi và thông điệp UX

## 15.1. AutoCAD đang bận

Thông báo:

> “AutoCAD đang có một lệnh chưa kết thúc. Tao chưa sửa gì. Mày hoàn tất hoặc hủy lệnh trong AutoCAD rồi thử lại.”

Tùy chính sách, job có thể chờ ngắn hạn nhưng không tự gửi ESC.

## 15.2. Dispatcher thiếu

> “Agent đã thấy AutoCAD nhưng document này chưa nạp dispatcher. Mở Desktop Agent và chọn ‘Sửa kết nối AutoCAD’.”

## 15.3. Bản vẽ thay đổi sau preview

> “Preview được tạo từ phiên bản cũ của bản vẽ. Tao đã chặn commit để tránh sửa nhầm. Cần tạo preview mới.”

## 15.4. Thiết bị offline

> “PC Xưởng đang offline. Không có thay đổi nào được thực hiện.”

## 15.5. Quyền không đủ

> “Tài khoản hiện chỉ có quyền đọc trên thiết bị này. Yêu cầu quản trị viên cấp quyền ghi.”

## 15.6. Không xác định được mục tiêu

ChatGPT phải hỏi người dùng hoặc yêu cầu selection; không được đoán và sửa hàng loạt.

## 15.7. Kết quả chưa xác định do mất mạng

> “Kết nối bị mất sau khi lệnh được gửi. Tao đang đối soát trạng thái; chưa thể khẳng định lệnh đã chạy hay chưa. Không gửi lại để tránh vẽ trùng.”

---

## 16. Vận hành hệ thống

## 16.1. Dịch vụ trung tâm

Tối thiểu gồm:

- FastMCP Gateway.
- Gateway domain services.
- PostgreSQL cho trạng thái bền.
- Redis hoặc message layer khi cần connection routing/queue.
- Artifact storage cho screenshot/preview nếu không lưu inline.
- Reverse proxy và HTTPS.
- Monitoring, logging và alerting.

## 16.2. Health và readiness

Hệ thống phải kiểm tra riêng:

- Gateway process.
- Database.
- Redis/message layer.
- Auth/JWKS.
- Artifact storage.
- Agent connection count.
- Queue latency.

## 16.3. Audit

Audit bắt buộc ghi:

- request/correlation ID;
- user ID;
- device ID;
- session/conversation context khi có;
- tool/capability;
- operation/risk;
- allow/deny;
- job state;
- duration;
- result/error code;
- approval actor;
- rollback relation.

Không ghi:

- access token;
- device secret;
- full private key;
- raw drawing content;
- base64 screenshot trong log;
- arbitrary path nếu chính sách yêu cầu che.

## 16.4. Hỗ trợ người dùng

Desktop Agent phải có chức năng xuất gói chẩn đoán gồm:

- Agent version.
- OS và AutoCAD version.
- Trạng thái dispatcher.
- Connection summary.
- Error codes gần đây.
- Log đã loại secret.

Người dùng phải xem trước nội dung trước khi gửi hỗ trợ.

## 16.5. Backup và khôi phục

- Backup database định kỳ.
- Artifact có retention riêng.
- Device credential không được backup ở dạng plaintext.
- Có quy trình revoke toàn bộ credential nếu có sự cố.

---

## 17. Bảo mật và quyền riêng tư

## 17.1. Bảo vệ danh tính

- Xác thực OAuth/OIDC.
- Validate issuer, audience, signature, expiry và scope.
- Device ownership kiểm tra độc lập với `device_id` client gửi.

## 17.2. Bảo vệ device

- Credential riêng cho từng device.
- Rotation và revoke.
- Lưu bằng hệ thống bảo mật Windows.
- Agent xác minh command envelope từ Gateway.
- Replay protection.

## 17.3. Bảo vệ bản vẽ

- Path allowlist fail-closed.
- Không tự upload toàn bộ DWG nếu không có tính năng và consent rõ ràng.
- Screenshot và snapshot chỉ lưu theo retention.
- Có tùy chọn không lưu artifact trên cloud lâu dài.

## 17.4. Giới hạn thực thi

Mỗi command có budget:

- số operation;
- số entity tạo/sửa/xóa;
- thời gian;
- payload;
- screenshot size;
- file export size;
- nesting/loop limit của CAD Program.

## 17.5. Prompt injection và dữ liệu trong bản vẽ

Text, attribute hoặc metadata trong DWG phải được coi là dữ liệu không tin cậy. Nội dung trong bản vẽ không được tự cấp quyền, thay đổi policy hoặc yêu cầu chạy code.

---

## 18. Yêu cầu phi chức năng

## 18.1. Độ tin cậy

- Không chạy trùng tác vụ ghi do retry mạng.
- Gateway restart không làm mất job bền.
- Agent reconnect phải reconcile trạng thái.
- Không tuyên bố thành công khi chưa có result hợp lệ.

## 18.2. Hiệu năng

Mục tiêu MVP tham khảo:

- Device online status cập nhật trong vòng 10–20 giây.
- Tool read đơn giản phản hồi trong vài giây khi AutoCAD ready.
- Job được giao tới Agent online trong vòng 2 giây ở tải bình thường.
- Progress không cập nhật dày hơn mức cần thiết.

Các con số cuối phải được chốt sau POC và benchmark.

## 18.3. Khả năng mở rộng

- Gateway không giữ trạng thái quan trọng chỉ trong memory.
- Có thể chạy nhiều Gateway instance.
- Connection routing không phụ thuộc sticky session lâu dài.
- Artifact tách khỏi local disk của một instance.

## 18.4. Tương thích phiên bản

- Protocol version negotiation.
- Capability manifest.
- Gateway biết Agent minimum supported version.
- Operation mới không làm Agent cũ crash.
- Unsupported operation bị từ chối rõ ràng.

## 18.5. Khả năng sử dụng

- Người mới không cần kiến thức lập trình.
- Thông điệp lỗi có hành động tiếp theo.
- Không lạm dụng thuật ngữ kỹ thuật.
- Các thao tác nguy hiểm luôn chỉ rõ tác động.

## 18.6. Accessibility

Cổng web và Agent UI nên hỗ trợ:

- keyboard navigation;
- độ tương phản;
- text scaling;
- trạng thái không chỉ phân biệt bằng màu;
- thông báo có nội dung rõ ràng.

---

## 19. Metrics sản phẩm và vận hành

### Product metrics

- Tỷ lệ cài đặt thành công.
- Tỷ lệ ghép thiết bị thành công.
- Thời gian từ đăng ký đến lệnh đầu tiên.
- Tỷ lệ job hoàn tất.
- Tỷ lệ preview được duyệt.
- Tỷ lệ rollback.
- Các error code phổ biến.
- Tỷ lệ người dùng quay lại.

### Reliability metrics

- Agent online count.
- Connection churn.
- Job queue latency.
- Delivery latency.
- Duplicate prevented count.
- Revision conflict count.
- Unknown result count.
- Gateway error rate.

### Privacy rule

Metrics không mặc định thu thập nội dung bản vẽ hoặc prompt đầy đủ. Telemetry chi tiết phải có chính sách và consent phù hợp.

---

## 20. Phạm vi MVP

MVP chỉ cần chứng minh sản phẩm nhiều người dùng hoạt động an toàn.

### Bắt buộc

- Một Central Gateway.
- FastMCP endpoint cho ChatGPT.
- Auth0 login.
- Hai user độc lập.
- Mỗi user có ít nhất một device.
- Desktop Agent Windows.
- Outbound persistent connection.
- Device enrollment và revoke.
- List/select device theo cuộc trò chuyện.
- AutoCAD status và active document.
- Observe/query cơ bản.
- Một workflow đọc.
- Một write operation an toàn.
- CAD Program v0 nhỏ.
- Preview, approval, revision check.
- Queue tuần tự theo device.
- Idempotency và reconnect cơ bản.
- Audit.
- Rollback/Undo Group cho operation thử nghiệm.
- User A không thể thấy hoặc gọi device của User B.

### Chưa cần trong MVP

- Billing hoàn chỉnh.
- Organization phức tạp.
- Kubernetes.
- Multi-region.
- Scene graph nâng cao.
- CAD DSL đầy đủ.
- Marketplace skill.
- Auto-update không giám sát hoàn toàn.
- Mobile management app.
- Offline queue dài hạn.

---

## 21. Phạm vi Production

Trước khi bán cho người dùng thật cần bổ sung:

- Installer ký số.
- Auto-update và rollback update.
- Rate limit/quota.
- Permission chi tiết.
- MFA/admin policies.
- Credential rotation.
- Backup/restore đã diễn tập.
- Monitoring và alerting.
- Security review.
- Load test.
- Chaos/reconnect test.
- Retention policy.
- Support diagnostics.
- Terms/privacy.
- Organization/team nếu bán B2B.
- Ma trận AutoCAD version.
- Quy trình xử lý incident và revoke toàn hệ thống.

---

## 22. Tiêu chí nghiệm thu end-to-end

### AC-01: Cài đặt

Một người dùng Windows mới cài Agent và ghép thiết bị mà không sửa `.env`, không mở port và không chạy PowerShell thủ công.

### AC-02: Cô lập người dùng

User A không thấy device của User B trong ChatGPT, API, cổng web hoặc qua việc đoán ID.

### AC-03: Chọn thiết bị

Một user có hai máy có thể chọn PC Xưởng trong cuộc trò chuyện A và Laptop trong cuộc trò chuyện B mà không ảnh hưởng nhau.

### AC-04: Quan sát

ChatGPT trả đúng active document, revision và entity summary của máy đã chọn.

### AC-05: Preview

Một write program tạo preview và báo đúng số entity dự kiến tạo/sửa/xóa trước khi commit.

### AC-06: Revision conflict

Nếu người dùng sửa bản vẽ sau preview, commit cũ bị chặn.

### AC-07: Approval binding

Approval của preview A không dùng được cho preview B, document khác hoặc device khác.

### AC-08: Queue

Hai write job cùng device không chạy đồng thời.

### AC-09: Duplicate prevention

Gateway retry cùng idempotency key không tạo geometry hai lần.

### AC-10: Reconnect

Agent mất mạng và kết nối lại có thể báo chính xác job cuối, không tự chạy lại job đã hoàn tất.

### AC-11: Emergency disconnect

Khi người dùng pause Agent, Gateway từ chối command mới ngay lập tức.

### AC-12: Rollback

Một job ghi thành công trong điều kiện hợp lệ có thể rollback và tạo audit relation.

### AC-13: Audit

Mọi job có user, device, document, operation, result và duration; log không chứa token hoặc secret.

### AC-14: AutoCAD busy

Khi AutoCAD đang có lệnh, MCP không gửi ESC và báo người dùng rõ ràng.

### AC-15: Arbitrary code

Mọi yêu cầu remote chạy AutoLISP/Python/shell tùy ý bị chặn không thể bypass bằng prompt.

---

## 23. Ngoài phạm vi

- Thay thế hoàn toàn giao diện AutoCAD.
- Cho phép điều khiển máy tính nói chung.
- Remote desktop.
- Arbitrary code execution.
- Đồng bộ file DWG cloud đầy đủ nếu chưa có sản phẩm riêng.
- Tự chịu trách nhiệm cho quyết định thiết kế kỹ thuật quan trọng mà không cần người dùng duyệt.
- Cam kết nhận dạng mọi loại bản vẽ ngay phiên bản đầu.

---

## 24. Quyết định sản phẩm cần chủ dự án chốt

1. Tên thương mại của Desktop Agent và cổng web.
2. MVP chỉ hỗ trợ AutoCAD LT 2024+ hay thêm AutoCAD đầy đủ.
3. Mặc định read-only hay write enabled sau enrollment.
4. Low-risk write có được chạy không cần preview hay không.
5. Thời gian lưu screenshot/preview.
6. Có cho phép người dùng tắt lưu artifact cloud hay không.
7. Số device tối đa mỗi tài khoản.
8. Có organization/team trong bản đầu hay để sau.
9. Chính sách giữ local MCP mode.
10. Các workflow chuyên ngành đầu tiên.
11. Mức độ hiển thị CAD Program cho người dùng phổ thông.
12. Chính sách update bắt buộc với Agent quá cũ.
13. Mức audit mà quản trị viên tổ chức được xem.
14. Tác vụ file open/save nào được phép remote.
15. Mô hình tính phí sau MVP.

---

## 25. Định nghĩa hoàn thiện sản phẩm

Sản phẩm được xem là hoàn thiện ở mức nền tảng khi:

- người dùng không kỹ thuật có thể tự cài và ghép máy;
- ChatGPT điều khiển đúng AutoCAD của đúng người dùng;
- workflow phổ biến hoạt động nhanh;
- CAD Program xử lý được yêu cầu mới mà không cần tạo public tool mới;
- mọi thay đổi quan trọng có preview, approval, revision check và rollback;
- mất mạng hoặc retry không gây chạy lặp âm thầm;
- người dùng kiểm soát được quyền remote ngay trên máy;
- hệ thống có audit và vận hành được ở mô hình nhiều Gateway/Agent;
- local mode hiện tại vẫn có đường tương thích hợp lý.

Kết quả cuối cùng phải mang lại trải nghiệm:

> “Tao nói việc cần làm trong ChatGPT, hệ thống hiểu đúng bản vẽ trên đúng máy, cho tao xem trước, chỉ sửa khi được phép, và tao luôn biết nó đã làm gì.”
