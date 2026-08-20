# Sổ Tay Xử Lý Lỗi Thường Gặp — AutoCAD FastMCP & Desktop Agent

Tài liệu tổng hợp các lỗi thực tế đã phát hiện, nguyên nhân gốc rễ và giải pháp khắc phục chi tiết trong quá trình cài đặt, build và vận hành hệ sinh thái **AutoCAD FastMCP, Desktop Agent, Web Portal và Gateway**.

---

## Mục lục
1. [Lỗi khi Build & Biên dịch (Build & Compilation Errors)](#1-lỗi-khi-build--biên-dịch)
2. [Lỗi Môi trường & PowerShell (Environment & Shell Errors)](#2-lỗi-môi-trường--powershell)
3. [Lỗi Xác thực & Liên kết Tài khoản (OIDC, OAuth & Pairing Errors)](#3-lỗi-xác-thực--liên-kết-tài-khoản)
4. [Lỗi Kết nối Desktop Agent & AutoCAD (Runtime & Connection Errors)](#4-lỗi-kết-nối-desktop-agent--autocad)
5. [Lỗi Phân quyền Đọc/Ghi (Read/Write Permissions & Approval)](#5-lỗi-phân-quyền-đọcghi)

---

## 1. Lỗi khi Build & Biên dịch

### 1.1. Lỗi `utf8NoBOM` khi build Managed .NET Host
* **Triệu chứng:**
  ```text
  Cannot bind parameter 'Encoding'. Cannot convert value "utf8NoBOM" to type "Microsoft.PowerShell.Commands.FileSystemCmdletProviderEncoding"...
  ```
* **Nguyên nhân:** Windows PowerShell 5.1 tích hợp sẵn trong Windows không hỗ trợ tham số `-Encoding utf8NoBOM` (tính năng này chỉ có từ PowerShell 7+ `pwsh`).
* **Giải pháp:** 
  * Chạy các script build bằng PowerShell 7:
    ```powershell
    pwsh -ExecutionPolicy Bypass -File .\scripts\build-phase8-r25-host.ps1
    ```
  * Hoặc dùng `utf8` / `[System.IO.File]::WriteAllText` trong code script.

---

### 1.2. Lỗi Nuitka không tương thích với phiên bản MSVC cũ
* **Triệu chứng:**
  ```text
  Nuitka-Scons: For Python version 3.12 MSVC 14.3 or later is required, not 14.2 which is too old.
  FATAL: Error, cannot locate suitable C compiler.
  ```
* **Nguyên nhân:** Nuitka tự động phát hiện bộ công cụ Visual Studio 2019 (MSVC 14.2), nhưng Python 3.12 yêu cầu tối thiểu MSVC 14.3 (VS 2022) hoặc MinGW64.
* **Giải pháp:** 
  * Truyền tường minh cờ `-Compiler mingw64` để Nuitka sử dụng trình biên dịch GCC 14.2 có sẵn:
    ```powershell
    pwsh -ExecutionPolicy Bypass -File .\scripts\build-phase5-agent.ps1 -Compiler mingw64
    ```

---

## 2. Lỗi Môi trường & PowerShell

### 2.1. Lỗi `$PSScriptRoot` bị rỗng trong Param Block
* **Triệu chứng:**
  ```text
  Join-Path : Cannot bind argument to parameter 'Path' because it is an empty string.
  At ...\run-phase5-agent.ps1: [string]$AgentExe = (Join-Path $PSScriptRoot ...)
  ```
* **Nguyên nhân:** Trong PowerShell 5.1, biến môi trường đặc biệt `$PSScriptRoot` chưa được khởi tạo khi đánh giá các giá trị mặc định trong khối `param()`.
* **Giải pháp:** Đặt giá trị mặc định là chuỗi rỗng trong `param()`, sau đó kiểm tra và gán giá trị với `$PSScriptRoot` ở phần thân script.

---

### 2.2. Lỗi `Start-Process` khi truyền mảng tham số rỗng `-ArgumentList @()`
* **Triệu chứng:**
  ```text
  Cannot validate argument on parameter 'ArgumentList'. The argument is null, empty, or an element of the argument collection contains a null value.
  ```
* **Nguyên nhân:** Lệnh `Start-Process` trên PowerShell 5.1 không chấp nhận mảng rỗng `@()`.
* **Giải pháp:** Kiểm tra điều kiện `$arguments.Count -gt 0` trước khi truyền `-ArgumentList`:
  ```powershell
  if ($arguments.Count -gt 0) {
      Start-Process -FilePath $AgentExe -ArgumentList $arguments -PassThru -Wait
  } else {
      Start-Process -FilePath $AgentExe -PassThru -Wait
  }
  ```

---

### 2.3. Lỗi mã hóa ký tự tiếng Việt trên Windows Console (`UnicodeEncodeError`)
* **Triệu chứng:**
  ```text
  UnicodeEncodeError: 'charmap' codec can't encode character '\u1ea7' in position...
  ```
* **Nguyên nhân:** Console Windows sử dụng bảng mã mặc định (cp1252 / cp437) không hiển thị được các ký tự tiếng Việt có dấu (như trong chuỗi *"Máy AutoCAD Khầy"*).
* **Giải pháp:** Cấu hình chuẩn UTF-8 trong Python và môi trường:
  ```python
  import sys
  if sys.stdout and hasattr(sys.stdout, "reconfigure"):
      sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  ```

---

### 2.4. Lỗi file cấu hình JSON chứa UTF-8 BOM
* **Triệu chứng:**
  ```text
  JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
  ```
* **Nguyên nhân:** Lệnh `Set-Content` của PowerShell tự động thêm Byte Order Mark (BOM) vào đầu file `.json`.
* **Giải pháp:** Sử dụng `encoding="utf-8-sig"` khi mở file trong Python:
  ```python
  with open(config_file, "r", encoding="utf-8-sig") as f:
      config_data = json.load(f)
  ```

---

## 3. Lỗi Xác thực & Liên kết Tài khoản

### 3.1. Lỗi HTTP ERROR 500 (`OIDC_AUTH_TIME_INVALID`) khi đăng nhập Google/Auth0
* **Triệu chứng:** Sau khi đăng nhập Google, trang callback `https://cad.kythuatvang.com/api/auth/callback` báo **HTTP ERROR 500**.
* **Nguyên nhân:** Đồng hồ trên máy chủ Gateway (VPS) bị chạy chậm hơn 3 phút so với thời gian thực của Auth0/Google. Kiểm tra bảo mật OIDC phát hiện `auth_time` nằm trong tương lai và ném lỗi `OIDC_AUTH_TIME_INVALID`.
* **Giải pháp:** Đồng bộ lại đồng hồ VPS về thời gian chuẩn UTC:
  ```bash
  date -u -s "$(curl -sI https://google.com | grep -i '^Date:' | cut -d' ' -f2-)"
  systemctl restart autocad-mcp-phase10-portal-7fdac59.service
  ```

---

### 3.2. Lỗi `device_already_paired` (Status 409 Conflict)
* **Triệu chứng:** Script liên kết báo lỗi `pairing_request_failed` do Gateway trả về `409 Conflict: {"error":"device_already_paired"}`.
* **Nguyên nhân:** Mã định danh thiết bị hiện tại (`device_id`) đã từng được liên kết trước đó.
* **Giải pháp:** Khi cần liên kết với tài khoản mới, sao lưu thư mục `identity` cũ để tạo cặp khóa Ed25519 và `device_id` hoàn toàn mới trước khi gọi `api.start()`.

---

## 4. Lỗi Kết nối Desktop Agent & AutoCAD

### 4.1. Desktop Agent báo "Chưa mở" / "Chưa kiểm tra" dù AutoCAD đang chạy
* **Triệu chứng:** Trên giao diện Desktop Agent, mục **AutoCAD** báo *Chưa mở*, **Bản vẽ** báo *Chưa có*.
* **Nguyên nhân:** 
  1. Chưa mở bản vẽ `.dwg` nào trong AutoCAD.
  2. Chưa nạp plugin `.NET Managed Host` (`AutocadMcp.Host.R25.dll`).
* **Giải pháp:**
  1. Mở một bản vẽ trong AutoCAD (ví dụ: `drawing33.dwg`).
  2. Trong AutoCAD, gõ lệnh `NETLOAD` và chọn file:
     `C:\Users\Admin\AppData\Roaming\Autodesk\ApplicationPlugins\AutocadMcp.ManagedHost.R25.bundle\Contents\R25\AutocadMcp.Host.R25.dll`
     *(hoặc gõ lệnh `AUTOCADMCPSTATUS`)*.
  3. Bấm nút **"Thử lại"** trên giao diện Desktop Agent.

---

### 4.2. Xung đột tiến trình Desktop Agent chạy ngầm
* **Triệu chứng:** Khởi động Agent mới nhưng giao diện không hiện hoặc không nhận cấu hình mới.
* **Nguyên nhân:** Tiến trình cũ (`KythuatvangAutoCADAgent.exe`) vẫn đang chạy ẩn trong Khay hệ thống (System Tray).
* **Giải pháp:** Tự động tắt sạch tiến trình cũ trước khi chạy mới (đã được tích hợp sẵn trong [`start_desktop_agent.bat`](file:///h:/AI/autocad-fastmcp/start_desktop_agent.bat)).

---

## 5. Lỗi Phân quyền Đọc/Ghi

### 5.1. Agent chỉ có quyền Đọc (Read-only), không vẽ được
* **Triệu chứng:** ChatGPT chỉ đọc được thông tin, khi yêu cầu vẽ đối tượng thì báo thiếu quyền hoặc không có tool ghi.
* **Nguyên nhân:** Desktop Agent chưa bật các cờ cho phép ghi của Phase 6 (`AUTOCAD_MCP_MANAGED_WRITE_ENABLED=1`, `AUTOCAD_AGENT_WRITE_LOCK_ENABLED=1`).
* **Giải pháp:** Khởi động Agent qua file [`start_desktop_agent.bat`](file:///h:/AI/autocad-fastmcp/start_desktop_agent.bat) (script đã được cấu hình tự động kích hoạt quyền ghi Managed Write và gán `AllowedDeviceId`).

---

### 5.2. Lệnh vẽ không tự động xuất hiện trên AutoCAD
* **Nguyên tắc an toàn (Trusted Approval):** Mọi thao tác ghi không được tự ý thực thi ngầm.
* **Quy trình:**
  1. ChatGPT tạo bản xem trước (`cad_preview`).
  2. Người dùng nhìn thấy yêu cầu tại mục **"Xác nhận tin cậy trên thiết bị"** trên giao diện Desktop Agent.
  3. Bấm nút **"Đồng ý đúng yêu cầu này"** trên Desktop Agent.
  4. Lệnh vẽ mới chính thức được ghi vào bản vẽ AutoCAD (`cad_commit`).
