# Phase 10 PR #14 — Handoff cho agent tiếp theo

Cập nhật: 2026-08-02

## Trạng thái bắt buộc

- Repo: `D:\AI\autocad-mcp`.
- Nhánh: `codex/phase-10-scene-graph-drawing-intelligence`.
- Không làm trên `main`; không triển khai Phase 11.
- PR: [#14](https://github.com/sontakmtp-cell/autocad-fastmcp/pull/14).
- Live stack đã pair thật qua Portal/Auth0; standalone Agent và Managed Host R25 đang online. Chưa được tuyên bố Engineering GO vì A/B/C, restart và DB no-effect vẫn phải được capture lại trên đúng implementation commit cuối.
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

- Gateway release remote đang chạy commit `6f3d0c5`; phải deploy lại đúng implementation commit cuối trước capture.
- systemd unit: `autocad-mcp-phase4.service`.
- Profile phải là `phase9_workflow` để bật Phase 9 public workflow và Phase 10 scene engine.
- Cloudflared unit: `autocad-mcp-cloudflared.service`.
- AutoCAD Mechanical 2025 đã báo `cad.host/1 local pipe ready`; phải kiểm tra lại PID và fixture đang mở trước từng capture.
- Agent standalone build: `dist\phase10-agent-pr14\app\KythuatvangAutoCADAgent.exe`.
- Device đã pair thật: `device-9bafc0c5-d0ac-48f8-bcf1-9f8fa94e7476`.
- Không stage các thư mục `.pytest-tmp-*`, `tmp/`, `acad.err`; không dùng `git add -A`.

## Blocker live đã được gỡ

- Portal đã được expose dưới public origin, Auth0/Google approval đã hoàn tất và Agent có session `cad.agent/2` thật.
- Bundle Managed Host đúng đã được build/cài; `AUTOCADMCPSTATUS` xác nhận R25 `0.8.0` và local pipe sẵn sàng.
- OAuth consent chủ đích xin đủ `autocad.read autocad.write autocad.device.manage`. Token có write scope không cho phép ghi trực tiếp: mọi write vẫn bắt buộc `prepare -> preview -> trusted approval -> commit -> validate`, kèm recovery/rollback.

## Việc agent sau phải làm

1. Commit/push toàn bộ code, test và tài liệu; deploy Gateway/Portal đúng implementation commit đó. Từ đây không thay code runtime cho tới khi evidence hoàn tất.
2. Chạy OAuth public read E2E với `--token-output tmp/phase10-live/token.json`. File token chỉ tồn tại trong lúc capture, không commit và phải xóa ngay sau đó.
3. Trên VM chạy `capture-identity` cho Gateway. Với **từng** Drawing A/B/C, người vận hành mở đúng DWG, đợi heartbeat rồi collect DB evidence `--session-only`; tại Windows chạy `capture-runtime-identity` và dùng đúng output đó làm `--process-identity` cho fixture tương ứng. Runtime identity phải bind đúng active document/revision nên không dùng chung một output cho ba bản vẽ.
4. Ngay sau mỗi runtime identity, chạy `capture-public` để tạo provisional artifact; capture theo thứ tự A, B, C để C vẫn active lúc restart. Không sửa JSON và không dùng artifact cũ.
5. Từ job/scene ID của ba provisional artifact, collect DB evidence **trước restart** với audit window bao trùm toàn bộ capture. Artifact phải `PASS`.
6. Chạy `capture-identity` trước restart; restart systemd Gateway thật; đợi cùng standalone Agent reconnect; chạy `capture-identity --old-pid ...`, collect lại DB evidence `--session-only` cho session mới rồi chạy `capture-runtime-identity`. Cuối cùng chạy `restart-query --runtime-identity-after <output-vừa-capture>`; process-before được derive trực tiếp từ fixture C, không tạo/sửa JSON process thủ công.
7. Collect DB evidence **sau restart** bằng cùng IDs và thêm `--pre-restart-evidence <pre.json>`. Producer phải chứng minh snapshot write trước/sau giống hệt; session sau restart khớp runtime identity theo thời điểm kết nối, active document/revision, protocol và phiên bản Agent.
8. Chạy `finalize-fixture` cho A/B/C và `finalize-restart`; mọi gate phải được tính lại từ raw evidence và artifact cuối phải `PASS`.
9. Xóa đúng file token tạm, chạy validator + regression matrix, commit chỉ retained evidence/status docs, push và đợi CI của head mới nhất xanh. Chỉ kết luận `Engineering GO` khi mọi mandatory gate audit được; `Customer Pilot` vẫn `NO-GO`.

## Safety không được vi phạm

- Không hard-code `true` cho restart/public query/no-effect; phải derive từ raw evidence.
- Không thay Managed .NET direct port cho đường Gateway → WSS → standalone Agent → RuntimeBroker → Managed Host → AutoCAD.
- Không CAD write, prepare/preview/commit, program/intent/approval/job/receipt hay DWG mutation trong scene inference.
- Không mở public destructive MCP tool, không hạ approval/risk floor, không bật LT write, không arbitrary execution, không bỏ owner/device/session binding.
- Nếu còn bất kỳ blocker mandatory nào: giữ `Engineering NO-GO`, ghi rõ blocker.
