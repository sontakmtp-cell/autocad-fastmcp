# Phase 5.6 — kiểm thử hai tài khoản bằng máy thật và VM

## Mục tiêu

Máy thật là Device A, VM `Phase4-Win11-Clean` là Device B. Hai thiết bị dùng
hai tài khoản Auth0 khác nhau. Cả hai chỉ được đọc bản vẽ; CAD write và arbitrary
code luôn tắt.

Telemetry collector chạy trên máy thật. VM chỉ gửi số liệu tổng hợp qua mạng
Hyper-V riêng.

VM không bắt buộc có AutoCAD cho bài identity pilot. Khi VM không có AutoCAD,
Device B chỉ chứng minh pairing, owner isolation, Agent/WSS và telemetry
transport; trạng thái đúng là `autocad_not_running`. Device A cung cấp lần đọc
AutoCAD thật, còn read behavior của Device B phải được bù bằng simulator
read-only tự động. Không trình bày biến thể này như hai AutoCAD thật.

## Việc cần chuẩn bị một lần trong Auth0

1. Trong Auth0 API có Identifier `https://cad.kythuatvang.com/mcp`, thêm permission
   `autocad.device.manage`.
2. Tạo một Auth0 Application loại Single Page Application cho Portal pilot.
3. Thêm các URL:
   - Allowed Callback URL:
     `https://cad.kythuatvang.com/api/auth/callback`
   - Allowed Logout URL: `https://cad.kythuatvang.com/`
   - Allowed Web Origin: `https://cad.kythuatvang.com`
4. Ghi lại Client ID. Không gửi Client Secret qua chat; SPA + PKCE không cần
   Client Secret.
5. Đảm bảo hai test user đều được phép nhận scope
   `autocad.device.manage`.

## Khởi động máy thật

Mở hai cửa sổ PowerShell thường:

```powershell
Set-Location D:\AI\autocad-mcp
.\scripts\run-phase5-identity-gateway.ps1
```

```powershell
Set-Location D:\AI\autocad-mcp
.\scripts\run-phase5-portal.ps1
```

Trước lần chạy Portal đầu tiên:

```powershell
.\scripts\provision-phase5-portal.ps1 -OAuthClientId '<CLIENT_ID_AUTH0>'
```

Mở PowerShell Administrator để cấu hình route Cloudflare:

```powershell
.\scripts\configure-phase5-cloudflare-routes.ps1
```

Sau đó khởi động lại named tunnel hiện có. Route Phase 5 dùng cùng hostname:

- Gateway: `/mcp`, `/agent/ws`, `/api/agent/*`, `/api/portal/*`,
  `/.well-known/*`;
- Portal: các đường dẫn web còn lại, gồm `/`, `/login`, `/pair`, `/devices`
  và `/api/auth/*`.

Rollback route:

```powershell
.\scripts\rollback-phase5-cloudflare-routes.ps1 `
  -BackupPath '<đường dẫn backup được lệnh configure in ra>'
```

## Collector trên máy thật

Làm theo
[phase5-local-telemetry-pilot-runbook.md](./phase5-local-telemetry-pilot-runbook.md).
Phải kiểm tra lại IP `vEthernet (Default Switch)` sau mỗi lần reboot.

## Device A trên máy thật

```powershell
.\scripts\provision-phase5-agent.ps1 `
  -GatewayWsUrl 'wss://cad.kythuatvang.com/agent/ws' `
  -GatewayHttpUrl 'https://cad.kythuatvang.com' `
  -PortalUrl 'https://cad.kythuatvang.com' `
  -DeviceName 'Device A - máy thật' `
  -TelemetryEndpoint 'http://<IP_DEFAULT_SWITCH>:4319/ingest/autocad-mcp'

.\scripts\run-phase5-agent.ps1
```

Lần đầu Agent tự mở trình duyệt. Đăng nhập User A và bấm xác nhận đúng tên
`Device A - máy thật`.

## Device B trong VM

Trước khi cài, tạo checkpoint VM:

```powershell
Checkpoint-VM `
  -Name Phase4-Win11-Clean `
  -SnapshotName before-phase56-two-user-pilot
```

Copy thư mục `dist\phase5-agent` vào
`C:\Phase5Pilot\phase5-agent` trong VM. Trong PowerShell của VM:

```powershell
Set-Location C:\Phase5Pilot\phase5-agent

.\provision-phase5-agent.ps1 `
  -GatewayWsUrl 'wss://cad.kythuatvang.com/agent/ws' `
  -GatewayHttpUrl 'https://cad.kythuatvang.com' `
  -PortalUrl 'https://cad.kythuatvang.com' `
  -DeviceName 'Device B - VM phase4lab' `
  -TelemetryEndpoint 'http://<IP_DEFAULT_SWITCH>:4319/ingest/autocad-mcp'

.\run-phase5-agent.ps1
```

Đăng nhập User B trong trình duyệt của VM và xác nhận đúng Device B. Không dùng
User A trong bước này.

## Bài kiểm thử bắt buộc

1. User A chỉ thấy Device A; User B chỉ thấy Device B.
2. Device A đọc health và summary của `drawing33.dwg`. Nếu Device B có AutoCAD
   thì đọc tương tự; nếu không, xác nhận `autocad_not_running` và chạy simulator
   read-only.
3. User B đoán URL/job/snapshot của A phải nhận `not_found`; không có command
   nào được gửi xuống Device A.
4. Revoke Device A từ Portal User A:
   - socket A đóng;
   - A không tự reconnect bằng khóa cũ;
   - Device B vẫn online; nếu VM không có AutoCAD thì Agent vẫn xác thực được
     và tiếp tục báo `autocad_not_running`.
5. Tắt collector rồi đọc lại:
   - CAD vẫn thành công;
   - Agent tăng bộ đếm export error/drop;
   - bật collector lại thì số liệu mới tiếp tục đến.
6. Dashboard chỉ có runtime/release/operation/outcome/safe error/count/latency;
   không có user ID, device ID, token, tên/path/nội dung bản vẽ.

Chỉ đánh dấu Phase 5.6 live E2E xanh khi đủ toàn bộ bằng chứng trên.
