# Phase 5.6 identity/isolation evidence

> Trạng thái 2026-07-26: **engineering và live two-user/two-device identity
> pilot xanh**. Device A là máy thật có AutoCAD; Device B là VM không cài
> AutoCAD nên chỉ chứng minh identity, Agent, socket và telemetry transport.
> Read-only behavior của Device B được kiểm bằng simulator. Live revoke/re-pair
> drill vẫn để lại cho lần vận hành tiếp theo.

## Đã xác minh tự động

| Gate | Kết quả |
|---|---|
| Stable owner `(issuer, subject)` | Xanh |
| Pairing code + polling secret + Ed25519 proof | Xanh |
| Pairing proof replay/key substitution/max attempts | Xanh |
| DPAPI current-user private key | Xanh |
| One-time device challenge và WSS token | Xanh |
| Portal OAuth Code + PKCE, HttpOnly encrypted session, CSRF/origin | Xanh |
| Dedicated `autocad.device.manage` scope | Xanh |
| Wrong owner device/job/snapshot/resource | `not_found` |
| Cross-owner request chặn trước Agent dispatch | Xanh |
| Revoke đóng đúng socket, invalidates token/challenge | Xanh |
| Revoke/handshake race | Xanh |
| Device B không bị ảnh hưởng khi revoke A | Xanh |
| Anonymous pairing/challenge rate and storage bounds | Xanh |
| OAuth/JWKS/public origin HTTPS only | Xanh |
| CAD write/arbitrary code | Tắt |
| AutoLISP/File IPC regression | Xanh |

## Kết quả test hiện tại

- Gateway: `192 passed`.
- Desktop Agent: `83 passed`.
- Shared contracts: `5 passed`.
- Portal: `11 passed`; Playwright `3 passed`, gồm navigation OAuth và
  two-owner URL isolation.
- Two-user/two-device simulator: `9 passed`.
- Phase 3 simulator regression: `33 passed`.
- Root AutoCAD MCP regression: `409 passed, 1 skipped`.
- Telemetry collector loopback: health `200`, ingest `202`, aggregate persisted,
  clean stop.

Các lượt nghiệm thu cuối dùng `--basetemp` riêng và đều exit `0`.

## Live evidence 2026-07-26

| Gate | Evidence |
|---|---|
| OAuth production scopes | Auth0 application cấp `autocad.read` và `autocad.device.manage`; Portal login thật thành công cho User A và User B |
| Pairing | User A chỉ thấy Device A; User B chỉ thấy Device B |
| Device A real read | Public ChatGPT connector đọc `drawing33.dwg`, `entity_count=30`, revision `1cff4e1e...30e5ff5` |
| Repeated read/idempotent state | Ba read-only observations liên tiếp trả cùng entity count và document revision |
| Owner isolation | Connector của User A chỉ list Device A; Portal của User B chỉ list Device B; automated direct URL/job/resource denial xanh |
| Device B without AutoCAD | Agent/WSS online; runtime báo `autocad_not_running`; không bị trình bày giả là CAD-ready |
| Telemetry | Host success và VM `autocad_not_running` được aggregate theo runtime/outcome, không chứa owner/device/drawing |
| Fail open | Khi collector dùng token cũ, exporter tăng `export_errors` nhưng Device A vẫn đọc đủ 30 entity |
| Recovery | Sau collector restart, exporter `export_errors=0` và live `drawing.observe.summary/succeeded` tăng count |
| Write boundary | Mọi live call đều read-only; write và arbitrary code vẫn tắt |

## Gate còn chủ động để mở

- Live revoke/re-pair một thiết bị và xác nhận thiết bị còn lại không gián đoạn.
- Nếu muốn hai **AutoCAD thật**, cần thêm license/máy AutoCAD thứ hai; không bắt
  buộc cho identity pilot hiện tại.
- Pilot telemetry 3–7 ngày trước khi coi telemetry production-ready.

Runbook:
[phase56-two-user-vm-pilot-runbook-vi.md](./phase56-two-user-vm-pilot-runbook-vi.md).
