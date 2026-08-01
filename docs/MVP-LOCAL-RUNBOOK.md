# Hướng dẫn chạy AutoCAD AI Connector (bản local)

Viết cho người dùng không cần biết code. Máy này đã có sẵn:

- Gateway (máy chủ trung gian) chạy ở `http://127.0.0.1:8000`
- Desktop Agent (ứng dụng kết nối AutoCAD với máy chủ)
- AutoCAD Mechanical 2025 + bản vẽ thử `drawing33.dwg`
- Managed Host R25 0.8.0 (plugin .NET đã cài vào Autodesk ApplicationPlugins)

## 1. Khởi động lại toàn bộ (khi cần)

Mở PowerShell (Start → gõ `powershell`) rồi chạy lần lượt:

```powershell
cd H:\AI\autocad-fastmcp

# 1) Máy chủ trung gian (giữ cửa sổ này mở)
powershell -ExecutionPolicy Bypass -File .\scripts\run-mvp-local-gateway.ps1
```

Mở một cửa sổ PowerShell khác:

```powershell
cd H:\AI\autocad-fastmcp

# 2) Desktop Agent (có giao diện)
powershell -ExecutionPolicy Bypass -File .\scripts\run-mvp-local-agent.ps1
```

3) Mở AutoCAD Mechanical 2025 và mở bản vẽ `H:\AI\autocad-fastmcp\drawing33.dwg`.

Khi agent kết nối được, trên giao diện sẽ hiện: máy chủ đã kết nối, AutoCAD đang chạy,
tên bản vẽ `drawing33.dwg`. Trong AutoCAD, gõ lệnh `AUTOCADMCPSTATUS` sẽ thấy:

```text
AutoCAD MCP: Managed Host R25 0.8.0; cad.host/1 local pipe ready.
```

> Lưu ý: hãy đóng AutoCAD bình thường (nút X hoặc lệnh QUIT). Nếu máy bị tắt
> đột ngột (treo, kill process), lần mở sau có thể không nạp plugin — script
> chạy Agent đã tự xóa cache nạp plugin của AutoCAD, nên chỉ cần chạy lại
> `run-mvp-local-agent.ps1` rồi mở lại AutoCAD là hết.

## 2. Kiểm tra nhanh máy chủ có sống không

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8000/healthz -UseBasicParsing
# Kết quả: ok
```

## 3. Chạy thử một lệnh đọc thật (bằng script kiểm tra)

Script này tự đăng ký máy, chờ agent kết nối rồi hỏi AutoCAD lấy tổng số đối tượng
trong bản vẽ đang mở:

```powershell
cd H:\AI\autocad-fastmcp
.\services\gateway\.venv\Scripts\python.exe .\scripts\verify-mvp-local-observe.py
```

Kết quả đúng sẽ có dòng `entity_count` (ví dụ `46` cho `drawing33.dwg`). Agent
đang chạy ở chế độ Managed Host, nên danh sách thiết bị cũng báo kèm gói
`autocad.managed_host.r25 0.8.0`.

## 4. Nối ChatGPT (bước sau)

Để ChatGPT điều khiển được, cần đưa Gateway ra Internet (OAuth + tunnel Cloudflare).
Phần này đang chờ các tài khoản/mật khẩu trong file `.env` ở thư mục gốc — file này
chưa có trên máy.

## 5. Việc đang dang dở

- Đường **đọc** qua Managed Host (đếm đối tượng, xem layer, geometry chính xác)
  đã hoạt động. Xem thêm `scripts/verify-mvp-local-observe.py`.
- **Scene Graph (Phase 10)** và **tạo đối tượng qua ChatGPT** cần Gateway công khai
  với OAuth — đang chờ file `.env` (tài khoản/mật khẩu) mày để ở máy khác.

## 6. Sửa nếu plugin Managed Host không nạp

Nếu gõ `AUTOCADMCPSTATUS` trong AutoCAD mà chỉ thấy "Managed Host is not ready":

1. Tắt AutoCAD (đóng bình thường).
2. Chạy lại script Agent (tự xóa cache nạp plugin):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-mvp-local-agent.ps1
```

3. Mở lại AutoCAD Mechanical 2025 → gõ `AUTOCADMCPSTATUS` → phải thấy
   "Managed Host R25 0.8.0; cad.host/1 local pipe ready."
