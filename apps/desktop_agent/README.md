# Desktop Agent

Agent Windows chỉ đọc cho một máy lab. Agent chủ động mở kết nối outbound tới
`/agent/ws`; không mở listener hoặc tunnel trên máy người dùng.

Phase 6 bổ sung đường CAD Program create-only riêng:

```text
ProgramCommandExecutor → RuntimeBroker.select_write_runtime
→ Managed .NET Host R25
```

Đường đọc Phase 3/4/5 vẫn dùng `ReadCommandExecutor`. CAD Program không fallback
sang AutoLISP hoặc AutoCAD LT. Write lock mặc định tắt; hard pause, exact binding,
per-document mutex, local ledger và `outcome_unknown` được Agent enforce trước
khi trả evidence về Gateway.

Các cờ Phase 6 phía Agent:

```text
AUTOCAD_MCP_PROGRAM_V0_ENABLED=0
AUTOCAD_MCP_MANAGED_WRITE_ENABLED=0
AUTOCAD_MCP_LT_WRITE_ENABLED=0
AUTOCAD_MCP_PHASE6_ALLOWED_DEVICE_IDS=
AUTOCAD_MCP_PROGRAM_POLICY_VERSION=
AUTOCAD_AGENT_WRITE_LOCK_ENABLED=0
```

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
