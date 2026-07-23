# Phase 4 C1 Desktop Agent

Agent Windows chỉ đọc cho một máy lab. Agent chủ động mở kết nối outbound tới
`/agent/ws`; không mở listener hoặc tunnel trên máy người dùng.

## Provision lab

```powershell
.\scripts\provision-phase4-agent.ps1 `
  -DeviceId device-lab `
  -GatewayWsUrl wss://cad.kythuatvang.com/agent/ws
```

Credential được hỏi bằng secure prompt và lưu bằng Windows DPAPI theo user hiện
tại. Script chỉ sao chép package vào `%LOCALAPPDATA%`; operator tự thêm đúng thư
mục package vào AutoCAD Support File Search Path/TRUSTEDPATHS.

## Chạy

```powershell
.\scripts\run-phase4-agent.ps1
```

Dùng `-Headless` khi kiểm thử không cần UI. UI mặc định có trạng thái máy chủ,
AutoCAD, basename bản vẽ, tác vụ, hard pause, retry, diagnostics và system tray.

## Build standalone folder

```powershell
.\scripts\build-phase4-agent.ps1
```

Artifact bàn giao là folder standalone, không phải installer hay auto-updater.
Folder này chứa sẵn `provision-phase4-agent.ps1`, `run-phase4-agent.ps1`, app và
package versioned; có thể sao chép sang máy lab mà không cần repo hoặc Python.
