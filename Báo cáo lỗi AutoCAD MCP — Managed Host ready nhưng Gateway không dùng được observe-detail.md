# Báo cáo lỗi AutoCAD MCP — Managed Host ready nhưng Gateway không dùng được observe/detail

## 1. Bối cảnh

Đang kiểm tra bản vẽ AutoCAD:

`XEGOONGRAIBT.dwg`

Mục tiêu ban đầu là tìm nguyên nhân các đường DIM vẫn tồn tại nhưng **không hiển thị số kích thước**.

Tuy nhiên quá trình kiểm tra bị chặn vì MCP hiện không đọc được dữ liệu chi tiết của entity `DIMENSION`.

Điểm quan trọng: **Managed .NET Host không bị chết.**

AutoCAD trực tiếp báo:

```text
Command: AUTOCADMCPSTATUS

AutoCAD MCP: Managed Host R25 0.8.0; cad.host/1 local pipe ready.
Managed Host R25 0.8.0; cad.host/1 local pipe ready.
```

AutoLISP package cũng đã load:

```text
APPLOAD mcp_dispatch.lsp successfully loaded.

=== MCP Dispatch v3.1 loaded ===
IPC directory: C:/temp/
Ready for commands via (c:mcp-dispatch)

=== MCP Dispatch v3.3-c1 read-only package loaded ===
```

Vì vậy **không được chẩn đoán rằng Managed Host không chạy**.

---

# 2. Trạng thái MCP mà ChatGPT nhìn thấy

`cad_list_devices()` trả về device online:

```text
device:
Máy AutoCAD Khầy

status:
online

document:
XEGOONGRAIBT.dwg

agent_version:
0.1.0

capabilities:
- cad.observe.detail-provenance/1
- observe

package_summary:
autocad.lisp.drawing_info
version 3.3-c1
```

Điểm bất thường là:

```text
Managed Host:
R25 0.8.0 READY

nhưng Gateway chỉ nhìn thấy:
autocad.lisp.drawing_info 3.3-c1
```

Không thấy capability tương ứng với Managed CAD Program như:

```text
cad.program.v1.compile
```

Thử:

```text
cad_list_devices(
    online_only=true,
    capability="cad.program.v1.compile"
)
```

kết quả:

```text
devices: []
```

Trong khi:

```text
cad_list_devices(
    online_only=true,
    capability="cad.observe.detail-provenance/1"
)
```

lại tìm thấy device hiện tại.

---

# 3. Summary observation hoạt động

Lệnh:

```text
cad_observe(
    observation_level="summary",
    include_preview_image=false
)
```

hoạt động bình thường.

Snapshot trả:

```text
document_name:
XEGOONGRAIBT.dwg

entity_count:
3781

layer_count:
17
```

Các layer gồm:

```text
0
AM_0
AM_0N
AM_7
c1
c2
center
CHINH
DEFPOINTS
DIM
DUONG BAO
HATCH
HIDDEN
khuat
LAYER1
TAM
TEXT
```

Nhưng snapshot đồng thời ghi:

```text
entity_summary:
    detail_available: false

revision_strength:
    summary_only

commit_safe:
    false
```

Đây là bằng chứng trực tiếp rằng snapshot hiện tại chỉ chứa dữ liệu tổng quan.

---

# 4. Detail observation bị lỗi

Thử:

```text
cad_observe(
    observation_level="detail",
    include_preview_image=false
)
```

Gateway tạo job thành công nhưng job chạy theo chuỗi:

```text
queued
↓
dispatched
↓
acknowledged
↓
failed
```

Lỗi:

```text
error_code:
backend_error

error_summary:
Agent reported a bounded CAD operation failure
```

Một lần chạy cụ thể:

```text
job-c2141440-076f-49a2-93e6-e887712915f8
```

Một lần trước đó:

```text
job-e69a01c0-fefb-46bb-9895-c3733b3c0470
```

Cả hai đều thất bại sau khi Desktop Agent đã acknowledge command.

Điều này rất quan trọng:

**Gateway → Agent hoạt động.**

Command đã tới Agent.

Failure xuất hiện **sau bước acknowledge**, tức nhiều khả năng nằm ở:

```text
Desktop Agent
→ RuntimeBroker
→ runtime adapter
→ Managed Host
```

hoặc quá trình chuyển kết quả từ Host về Agent.

---

# 5. Có sự mâu thuẫn về capability

Device công bố:

```text
cad.observe.detail-provenance/1
```

Nhưng khi thực sự gọi:

```text
cad_observe(detail)
```

thì operation thất bại.

Đây là một inconsistency cần điều tra.

Về mặt contract, một trong hai thứ đang sai:

### Trường hợp A

Agent **không thực sự hỗ trợ detail**, nhưng capability manifest lại công bố:

```text
cad.observe.detail-provenance/1
```

### Trường hợp B

Agent/Managed Host thực sự hỗ trợ detail, nhưng dispatch path của `cad_observe(detail)` đang lỗi.

Theo kiến trúc của dự án, Desktop Agent chịu trách nhiệm cho:

- heartbeat/presence;
- RuntimeBroker;
- local capability publication;
- routing runtime.



Do đó nên đặc biệt kiểm tra logic tạo capability manifest từ trạng thái RuntimeBroker.

---

# 6. Kiến trúc mong đợi theo tài liệu dự án

Architecture mô tả:

```text
Desktop Agent
    ↓
RuntimeBroker
    ├─ ManagedDotNetAdapter
    ├─ AutoLispFileIpcAdapter
    └─ EzdxfAdapter

ManagedDotNetAdapter
    ↓ cad.host/1 Named Pipe
Managed Host
    ↓
AutoCAD Managed API
```



Runtime selection được định nghĩa:

```text
Full + valid Managed Host
→ managed_dotnet
→ primary
→ full capability
```

Trong khi:

```text
Full + missing/mismatched Host
→ degraded
→ optional read fallback
```



Hiện tại AutoCAD xác nhận:

```text
Managed Host R25 0.8.0
cad.host/1 local pipe ready
```

nên kỳ vọng RuntimeBroker phải nhận ra:

```text
managed_dotnet = available
```

và chọn nó làm primary runtime.

Nhưng hành vi từ phía Gateway giống một hệ thống đang chạy bằng read-only LISP package:

```text
autocad.lisp.drawing_info 3.3-c1
```

---

# 7. Giả thuyết chính

## Giả thuyết 1 — RuntimeBroker cache trạng thái cũ

Khả năng cao nhất.

Có thể Desktop Agent khởi động trước khi Managed Host sẵn sàng.

Khi đó RuntimeBroker probe:

```text
Managed Host unavailable
```

và chọn:

```text
AutoLispFileIpcAdapter
```

Sau đó Managed Host được load và:

```text
AUTOCADMCPSTATUS
→ pipe ready
```

nhưng Agent không:

```text
reprobe host
```

hoặc không:

```text
refresh RuntimeBroker
```

hoặc không:

```text
republish capability manifest
```

Kết quả:

```text
AutoCAD:
Managed Host ready

Agent:
vẫn nghĩ runtime cũ

Gateway:
vẫn nhận capability/package cũ
```

Cần kiểm tra lifecycle:

```text
Agent startup
→ runtime probe
→ heartbeat
→ runtime changes
→ capability republish
```

Đặc biệt kiểm tra xem runtime probe chỉ chạy một lần lúc startup hay có được refresh định kỳ.

---

# 8. Giả thuyết 2 — Health check và observation path dùng hai cơ chế khác nhau

`AUTOCADMCPSTATUS` chỉ chứng minh:

```text
Host process/plugin hoạt động
+
Named Pipe server tồn tại
```

Nó chưa chứng minh Desktop Agent đang kết nối được vào pipe.

Có thể:

```text
AutoCAD plugin
→ Host status OK
```

nhưng:

```text
Desktop Agent
→ Named Pipe connect
```

bị lỗi bởi:

- pipe name mismatch;
- protocol/version mismatch;
- authentication/session token mismatch;
- Windows user/session mismatch;
- stale pipe endpoint;
- handshake failure.

Cần log riêng:

```text
ManagedDotNetAdapter.connect()
ManagedDotNetAdapter.health()
cad.host/1 handshake
protocol version
host version
session/authentication
```

---

# 9. Giả thuyết 3 — Managed Host được phát hiện nhưng observe vẫn bị route sang LISP

Có thể RuntimeBroker có logic kiểu:

```text
if operation == "observe":
    use AutoLispFileIpcAdapter
```

trong khi:

```text
preview / commit
→ ManagedDotNetAdapter
```

Nếu vậy thì Host vẫn hoàn toàn khỏe nhưng `cad_observe()` không bao giờ đi qua Host.

Điều này cần đối chiếu với kiến trúc hiện tại.

Architecture nói Managed Host đã triển khai:

```text
health
observe
entity paging/events
document identity/revision
```



Do đó detail/entity observation theo thiết kế **có tồn tại ở Managed Host**.

Cần kiểm tra routing cho:

```text
observe summary
observe detail
entity paging
cad_query
```

Có thể `summary` cố ý dùng LISP nhưng `detail` cần Managed Host, và branch chuyển runtime đang lỗi.

---

# 10. Giả thuyết 4 — Capability publication bị sai

Có thể RuntimeBroker đúng nhưng capability mapper sai.

Hiện device publish:

```text
cad.observe.detail-provenance/1
```

dù detail operation không chạy được.

Có thể capability này được add tĩnh:

```python
capabilities.add("cad.observe.detail-provenance/1")
```

thay vì dựa vào:

```text
actual selected runtime
+
successful Host health/handshake
+
operation registry
```

Capability chỉ nên được publish nếu operation thực sự executable.

Cần kiểm tra code tạo:

```text
device capabilities
runtime manifest
package summary
heartbeat payload
```

---

# 11. Giả thuyết 5 — Package summary không phản ánh runtime thực

Hiện Gateway trả:

```text
package_summary:
autocad.lisp.drawing_info 3.3-c1
```

Điều này có thể có hai cách giải thích.

### Cách 1

Agent thực sự đang chạy LISP runtime.

### Cách 2

`package_summary` chỉ là danh sách package read-only được cài, không phải selected runtime.

Nếu là cách 2 thì không được dùng `package_summary` để suy ra runtime đang active.

Nên kiểm tra schema và bổ sung explicit runtime evidence như:

```json
{
  "selected_runtime": "managed_dotnet",
  "runtime_role": "primary",
  "managed_host": {
    "connected": true,
    "version": "0.8.0",
    "protocol": "cad.host/1"
  }
}
```

Việc này cũng giúp debug sau này rất nhiều.

---

# 12. Giả thuyết 6 — `cad_query` bị chặn vì snapshot summary-only

`cad_query` với:

```text
types=["DIMENSION"]
```

trả:

```text
capability_missing
```

Điều này có thể không phải bug riêng của `cad_query`.

Có khả năng:

```text
summary snapshot
→ không chứa entity projection
→ cad_query không thể hoạt động
```

Do `cad_observe(detail)` đã fail nên snapshot usable cho entity query chưa từng được tạo.

Vì vậy ưu tiên sửa:

```text
cad_observe(detail)
```

trước.

Sau khi detail snapshot thành công mới đánh giá `cad_query`.

---

# 13. Scene engine cũng xác nhận snapshot không có entity chi tiết

Thử build scene từ summary snapshot:

```text
cad_build_scene(...)
```

operation thành công nhưng scene trả:

```text
nodes: 0
relations: 0
contours: 0
features: 0
issues: 0
evidence: 0
```

Điều này phù hợp với:

```text
detail_available = false
```

Nó không phải lỗi scene engine.

Scene engine đơn giản là không có entity projection để phân tích.

---

# 14. Những module nên kiểm tra trước

Dựa trên kiến trúc repo, ưu tiên kiểm tra những khu vực sau.

## Desktop Agent RuntimeBroker

Tìm:

```text
RuntimeBroker
ManagedDotNetAdapter
AutoLispFileIpcAdapter
runtime selection
runtime health
runtime refresh
capability publication
```

Architecture xác nhận RuntimeBroker nằm trong Desktop Agent và là nơi lựa chọn adapter. 

Repo cũng có:

```text
apps/desktop_agent/src/autocad_desktop_agent/runtime/
```

và search trước đó tìm thấy:

```text
apps/desktop_agent/src/autocad_desktop_agent/runtime/autolisp_file_ipc.py
```

Cần tìm Managed counterpart của file này.

---

## Managed Host

Kiểm tra:

```text
native/autocad_managed_host/
```

Đặc biệt:

```text
health
observe
entity projection
entity paging
document identity
Named Pipe request dispatch
```

Architecture xác nhận Managed Host có trách nhiệm cho:

```text
AutoCAD process/document context
transactions
entity/object adapters
event/revision evidence
operation registry
```



---

## Gateway device registration / heartbeat

Kiểm tra nơi Agent gửi:

```text
device state
capabilities
runtime info
package info
```

Xem capability có được:

```text
calculated once
```

hay:

```text
updated every heartbeat
```

Nếu Host state thay đổi thì Gateway cần nhận capability mới.

---

# 15. Logging nên bổ sung

Hiện lỗi:

```text
Agent reported a bounded CAD operation failure
```

quá chung chung.

Gateway chỉ biết:

```text
backend_error
```

Trong khi lỗi thật có thể là:

```text
managed host not selected
pipe unavailable
handshake rejected
unsupported operation
entity projection exception
AutoCAD transaction error
document context error
```

Nên Agent log nội bộ đầy đủ:

```text
operation
selected runtime
adapter
host health
host version
protocol
document
exception type
exception message
correlation_id
command_id
```

Ví dụ:

```text
observe.detail
runtime=managed_dotnet
host=R25/0.8.0
protocol=cad.host/1
document=XEGOONGRAIBT.dwg
result=failed
exception=...
```

Gateway vẫn có thể trả bounded public error, nhưng local Agent log phải đủ chi tiết.

---

# 16. Test reproduction

## Điều kiện

AutoCAD mở:

```text
XEGOONGRAIBT.dwg
```

AutoCAD command:

```text
AUTOCADMCPSTATUS
```

phải trả:

```text
Managed Host R25 0.8.0
cad.host/1 local pipe ready
```

---

## Test 1 — Device manifest

Gọi:

```text
cad_list_devices()
```

Kiểm tra:

```text
selected runtime
Managed Host state
capabilities
package summary
```

Hiện trạng lỗi:

```text
capabilities:
cad.observe.detail-provenance/1
observe

package:
autocad.lisp.drawing_info 3.3-c1
```

---

## Test 2 — Summary observe

```text
cad_observe(summary)
```

Expected hiện tại:

```text
PASS
3781 entities
```

---

## Test 3 — Detail observe

```text
cad_observe(detail)
```

Hiện tại:

```text
FAIL
backend_error
```

Expected sau khi sửa:

```text
PASS

detail_available=true
```

---

## Test 4 — Query DIMENSION

Sau khi có detail snapshot:

```text
cad_query(
    snapshot_id,
    types=["DIMENSION"]
)
```

Expected:

```text
danh sách Dimension entities
```

Không được:

```text
capability_missing
```

---

# 17. Acceptance criteria

Bug chỉ được xem là sửa xong khi toàn bộ điều kiện sau đạt.

### Runtime

AutoCAD:

```text
AUTOCADMCPSTATUS
→ Managed Host ready
```

Agent/Gateway cũng phải phản ánh:

```text
selected_runtime = managed_dotnet
```

---

### Capability

Capability phải tương ứng với runtime thực.

Không được xảy ra tình trạng:

```text
advertised capability
→ operation always fails
```

---

### Detail observation

```text
cad_observe(detail)
```

phải thành công.

Snapshot phải có:

```text
detail_available=true
```

---

### Entity query

```text
cad_query(types=["DIMENSION"])
```

phải trả entity.

---

### Runtime evidence

Khuyến nghị response của observation có:

```text
runtime=managed_dotnet
host_version=0.8.0
protocol=cad.host/1
adapter=ManagedDotNetAdapter
```

để những lần debug sau không cần suy đoán.

---

# 18. Sau khi MCP detail hoạt động mới quay lại lỗi DIM

Mục tiêu ban đầu vẫn là tìm nguyên nhân:

```text
đường DIM tồn tại
nhưng số kích thước không hiển thị
```

Khi `cad_query(DIMENSION)` hoạt động, cần đọc ít nhất:

```text
entity id
dimension type
measurement
dimension style
DimensionText / TextOverride
TextHeight
TextPosition
TextStyle
layer
visibility
annotative state
annotation scale
DIMTXT
DIMSCALE
DIMLFAC
DIMZIN
zero suppression
```

Sau đó phân loại DIM lỗi.

Ví dụ:

```text
Measurement != 0
+
dimension graphics visible
+
dimension text invisible
```

rồi xác định nguyên nhân thật sự.

**Không nên sửa DIM trước khi entity observation hoạt động**, vì lúc đó chỉ đang đoán.

---

# 19. Kết luận

Điều đã xác nhận:

```text
Managed Host R25 0.8.0:
READY

cad.host/1:
READY

Desktop Agent:
ONLINE

Gateway:
ONLINE

summary observe:
WORKS

detail observe:
FAILS

entity query:
UNAVAILABLE

Gateway-visible capability/runtime information:
không phản ánh đầy đủ Managed Host
```

Phán đoán ưu tiên:

```text
1. RuntimeBroker không refresh/rebind Managed Host
   hoặc

2. observe/detail dispatch không được route đúng vào ManagedDotNetAdapter
   hoặc

3. capability publication không khớp runtime thật
   hoặc

4. Agent → cad.host/1 handshake/operation dispatch bị lỗi dù Host server vẫn ready
```

Hãy debug từ:

```text
cad_observe(detail)
↓
Desktop Agent command handler
↓
RuntimeBroker selected runtime
↓
ManagedDotNetAdapter
↓
cad.host/1 request
↓
Managed Host observe/entity projection
```

Không tập trung vào LISP `mcp_dispatch.lsp` trước, vì LISP package hiện load thành công và `summary observe` đang hoạt động.

Mục tiêu đầu tiên là làm cho:

```text
cad_observe(detail)
```

trả một detail snapshot thành công từ `managed_dotnet`.

Sau đó mới tiếp tục điều tra lỗi số DIM không hiển thị.