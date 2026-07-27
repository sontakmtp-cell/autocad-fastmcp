# AutoCAD MCP Web Portal

Portal tối thiểu dùng Next.js App Router. Browser chỉ giữ cookie phiên `HttpOnly`;
OAuth token và địa chỉ Gateway chỉ được dùng trong BFF phía server.

## Chạy local

1. Cài Node.js 24.
2. Sao chép `.env.example` thành `.env.local` và điền cấu hình OIDC/Gateway.
3. Chạy `npm install`, rồi `npm run dev`.

Các biến `PORTAL_SESSION_SECRET`, `PORTAL_OIDC_CLIENT_SECRET` và
`PORTAL_GATEWAY_BASE_URL` không được đặt với tiền tố `NEXT_PUBLIC_`.

## Gateway contract

Typed client phía server gọi các endpoint owner-scoped:

- `GET /api/portal/v1/devices`
- `GET /api/portal/v1/devices/{id}`
- `POST /api/portal/v1/devices/{id}/revoke`
- `GET /api/portal/v1/pairings/{id}`
- `POST /api/portal/v1/pairings/{id}/confirm`
- `POST /api/portal/v1/pairings/{id}/deny`

Gateway phải lấy owner từ bearer token. Portal không gửi và không tin `user_id`
trong URL, query hoặc form.

Phase 6 bổ sung các endpoint đọc owner-scoped:

- `GET /api/portal/v1/programs/{program_id}/revisions/{revision}`
- `GET /api/portal/v1/previews/{preview_id}`
- `GET /api/portal/v1/jobs/{job_id}`
- `GET /api/portal/v1/receipts/{receipt_id}`
- `GET /api/portal/v1/validations/{validation_id}`

## Feature flags Phase 6

Các flag mặc định fail-closed:

- `PORTAL_PHASE6_UI_ENABLED=false`
- `PORTAL_MANAGED_WRITE_UI_ENABLED=false`
- `PORTAL_MANAGED_WRITE_KILL_SWITCH=true`

Đây là cấu hình hiển thị của Portal. Gateway và Desktop Agent vẫn là nơi
enforce allowlist, write lock, hard pause và Managed Write. Phase 6 không có
approval button, trusted confirmation, `confirm=true` hoặc nút retry write khi
`outcome_unknown`.

## Kiểm thử

- `npm test`: contract, CSRF/origin, owner-safe proxy và component.
- `npm run test:e2e`: hai browser context với mock Gateway, bao gồm đoán URL
  thiết bị/resource của owner khác và lifecycle summary Phase 6.
- `npm run test:evidence`: kiểm tra tĩnh feature flags fail-closed và xác nhận
  các route Phase 6 không có form/button mutation hay approval.
