# Phase 10 PR #14 — Handoff cho agent tiếp theo

Cập nhật: 2026-07-30

## Trạng thái bắt buộc

- Repo: `D:\AI\autocad-mcp`.
- Nhánh: `codex/phase-10-scene-graph-drawing-intelligence`.
- Không làm trên `main`; không triển khai Phase 11.
- PR: [#14](https://github.com/sontakmtp-cell/autocad-fastmcp/pull/14).
- Local đã khôi phục về `8663714` (đúng head PR #14 trên `origin`); bộ sửa 3 blocker dưới đây đang ở dạng thay đổi chưa commit trong working tree, chưa push.
- Chưa được tuyên bố Engineering GO. Trạng thái hiện tại là `Engineering NO-GO` vì thiếu live pairing/Agent evidence.
- `Customer Pilot NO-GO` vẫn giữ nguyên.

## Những gì đã làm được

Ba comment review trong PR là lỗi đúng và đã sửa:

1. Snapshot detail không an toàn khi rolling upgrade dù protocol vẫn là `cad.agent/2`.
2. Restart evidence tin vào JSON/PID do caller cung cấp và hard-code cờ thành công.
3. Validator tin `gate_results` thay vì tính lại từ raw evidence.

Các commit local:

- `2172940`: thêm negotiation `cad.observe-detail/2`; Agent chỉ gửi provenance khi Gateway đã negotiate; legacy snapshot bị giới hạn thành non-authoritative.
- `87171a7`: giữ tương thích summary observation trong Gateway C1.
- `c4ddd6f`: restart capture bắt buộc raw process/service identity, systemd MainPID, procfs start/exit, release/hash, public query payload và DB no-effect input.
- `cce444a`: validator tính lại raw A/B/C, identity/distinctness, restart, DB/no-effect; thêm tamper tests.
- `ecc915f`: validator bắt buộc schema restart authoritative mới và loại bỏ cờ legacy.
- `59fefa1`: cô lập dependency của live capture trong test.

Các correctness fix trước đó vẫn phải được giữ: tolerance verification, canonical scene reuse, scene section persistence, relation evidence identity, bounded heuristic `part`, contour/hole evidence cho auto-dimension, scene expiry lifecycle.

## Thứ tự deploy an toàn và phiên bản tối thiểu

Gateway và Agent vẫn cùng dùng protocol `cad.agent/2`; tương thích rolling upgrade dựa trên negotiation capability `cad.observe.detail-provenance/1`:

- Gateway cũ + Agent mới: Gateway cũ bỏ qua capability lạ, không gửi `detail_snapshot_contract` → Agent mới trả snapshot detail dạng legacy → Gateway cũ hoạt động bình thường.
- Gateway mới + Agent cũ: Agent không quảng cáo capability → Gateway không negotiate → Agent gửi dạng legacy → Gateway nhận và gắn provenance non-authoritative (`source_runtime=managed_dotnet_legacy`, `geometry_status=bounded_projection`); legacy không được cấp authority Phase 10.
- Gateway mới + Agent mới: negotiate `cad.observe-detail/2` → Agent gửi đủ 4 trường provenance exact → scene Phase 10 hoạt động đầy đủ.

Thứ tự an toàn: deploy Gateway trước rồi Agent (hoặc ngược lại đều không gãy), vì Gateway mới chấp nhận cả hai shape (dual-shape acceptance) và chỉ cấp Phase 10 authority khi có provenance exact thật.

Phiên bản tối thiểu: Agent `0.1.0` hiện tại vẫn dùng được với mọi Gateway (`cad.agent/2`); tính năng scene geometry Phase 10 chỉ hoạt động khi Agent quảng cáo `cad.observe.detail-provenance/1` VÀ Gateway negotiate `cad.observe-detail/2`.

## Test đã chạy và pass

```text
.\.venv\Scripts\python.exe -m pytest -q tests/test_phase10_live_evidence_validation.py tests/test_phase10_live_restart_evidence.py --basetemp .pytest-tmp-review3-focused
31 passed

services\gateway\.venv\Scripts\python.exe -m pytest -q services\gateway\tests\test_phase4_c1.py --basetemp services\gateway\.pytest-tmp-review3-c1
20 passed
```

Validator trên artifact restart cũ fail vì thiếu các gate schema mới. Đây là hành vi đúng; phải recapture live artifact mới.

Full Gateway pytest từng treo ở khoảng 124 giây và 244 giây sau thay đổi compatibility. Không chạy mù toàn bộ suite quá 6 phút; chia theo file/group và kiểm tra child process.

## Runtime hiện tại

- Gateway release remote: `/opt/autocad-mcp/releases/20260730T1625-phase10-pr14-59fefa1`.
- systemd unit: `autocad-mcp-phase4.service`.
- Profile phải là `phase9_workflow` để bật Phase 9 public workflow và Phase 10 scene engine.
- Cloudflared unit: `autocad-mcp-cloudflared.service`.
- AutoCAD Full/Mechanical 2025 đang dùng fixture A: `fixtures\phase10\live\phase10-drawing-a.dwg`; phải kiểm tra lại PID trước capture.
- Agent standalone build: `dist\phase10-agent-pr14\app\KythuatvangAutoCADAgent.exe`.
- Không stage các thư mục `.pytest-tmp-*`, `tmp/`, `acad.err`; không dùng `git add -A`.

## Blocker live đã xác nhận

Local pairing marker giả đã được di chuyển khỏi identity directory để không giả paired state:

```text
C:\Users\haing\AppData\Local\Kythuatvang\AutoCADAgent\paired-before-real-pairing-20260730.json
```

Một enrollment thật đã từng bị `429 rate_limited` vì còn pending session cũ; DB không bị xóa. Sau khi hết hạn, enrollment mới tạo thành công nhưng URL `/pair?...` trả `Not Found`.

Root cause đã xác minh bằng Cloudflare log:

- Tunnel hiện chỉ map `cad.kythuatvang.com` vào Gateway `127.0.0.1:8765`.
- Không có Portal web service/ingress đang chạy.
- `apps/web_portal` có source trong release nhưng không được expose dưới public origin.
- Gateway có API `/api/portal/v1/pairings/...`, nhưng không phục vụ web page `/pair`.

Không được coi pairing, Agent reconnect hoặc live evidence là thành công khi chưa thấy Portal/Auth0 approval thật và session-token/`cad.agent/2` Hello thật.

## Việc agent sau phải làm

1. Expose/deploy `apps/web_portal` dưới đúng `https://cad.kythuatvang.com` bằng cách nhỏ nhất có thể kiểm chứng; không thay đổi security boundary của Gateway.
2. Tạo enrollment mới sau khi pending session cũ hết hạn; dùng Portal/Auth0 thật để approve.
3. Chạy packaged standalone Agent với Managed .NET bật và AutoCAD R25 mở; ghi raw Gateway/Agent/Host/AutoCAD/device/session/document/revision/process identity.
4. Capture mới Drawing A/B/C bằng `scripts/phase10-live-public-evidence.py capture-public` (provisional) rồi `finalize-fixture`; không dùng lại artifact `device-0e4...` cũ làm live proof.
5. Capture fixture theo 2 phase: chạy `capture-public` (ghi tool invocation thật + provisional artifact chứa job/scene IDs), chờ audit window đóng rồi collect DB evidence bằng `phase10-live-db-evidence.py ... --implementation-commit <capture-commit>`, sau đó chạy `finalize-fixture --fixture-evidence <provisional> --no-effect-db <db>` để cross-bind (window bao trùm capture, `window_end <= db.captured_at`, session/job/scene trong DB, invocation graph chính xác) và phát ra artifact PASS. `capture` cũ không còn tồn tại để tránh dependency cycle fixture↔DB.
6. Chạy `capture-identity` trên VM trước khi restart (raw `systemctl show` + procfs) và sau khi restart kèm `--old-pid` (probe PID cũ đã thoát); chạy `restart-query` qua public scene path với `--identity-before/--identity-after` bắt buộc: build scene, stop Gateway process thật, start process mới, Agent reconnect, query scene cũ public. `restart-query` chỉ phát artifact `PROVISIONAL`; sau khi audit window đóng, collect DB evidence rồi chạy `finalize-restart --restart-evidence <provisional> --before <fixture-capture> --no-effect-db <db> --device-id <device> --output <final>`. Chỉ artifact `finalize-restart` có `status: PASS` mới được đưa vào validator. Script từ chối process JSON tự khai `gateway_service_record`/`old_gateway_process_exit`.
7. Chạy validator hardened và toàn bộ regression groups phù hợp.
8. Cập nhật `docs/architecture/Phase-10.md` với fixture/live/negative/restart/no-effect matrix, exact commands, commit SHA, CI và checklist GO.
9. Stage có chủ đích, commit, push branch và poll CI. Chỉ kết luận `Engineering GO` khi mọi mandatory gate đều có artifact audit được.

## Safety không được vi phạm

- Không hard-code `true` cho restart/public query/no-effect; phải derive từ raw evidence.
- Không thay Managed .NET direct port cho đường Gateway → WSS → standalone Agent → RuntimeBroker → Managed Host → AutoCAD.
- Không CAD write, prepare/preview/commit, program/intent/approval/job/receipt hay DWG mutation trong scene inference.
- Không mở public destructive MCP tool, không hạ approval/risk floor, không bật LT write, không arbitrary execution, không bỏ owner/device/session binding.
- Nếu còn bất kỳ blocker mandatory nào: giữ `Engineering NO-GO`, ghi rõ blocker.
