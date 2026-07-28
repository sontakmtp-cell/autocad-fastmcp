import Link from "next/link";
import type { Consent, ExecutionIntent } from "@/lib/contracts";
import type { RecentAuthState } from "@/lib/security";

const intentStateCopy: Record<ExecutionIntent["state"], string> = {
  awaiting_approval: "Đang chờ phê duyệt đáng tin cậy",
  ready: "Đã phê duyệt, đang chuẩn bị phát hành",
  released: "Đã phát hành cho hàng đợi thực thi",
  denied: "Đã từ chối",
  expired: "Phê duyệt đã hết hạn",
  invalidated: "Phê duyệt không còn hiệu lực",
  cancelled: "Đã hủy",
};

function safeTrustedSummary(value: string): string {
  return /(?:[A-Za-z]:\\|\\\\|bearer\s|access.?token|private.?key|password|secret)/i.test(value)
    ? "Chi tiết tác động đã được ẩn vì bảo mật"
    : value;
}

export function ConsentApproval({
  intent,
  consent,
  csrfToken,
  recentAuth,
  approvalEnabled,
  error,
  result,
}: {
  intent: ExecutionIntent;
  consent: Consent;
  csrfToken: string;
  recentAuth: RecentAuthState;
  approvalEnabled: boolean;
  error?: string;
  result?: string;
}) {
  const pending = intent.state === "awaiting_approval"
    && consent.state === "requested"
    && Date.parse(intent.expires_at) > Date.now()
    && Date.parse(consent.expires_at) > Date.now();
  const canDecide = approvalEnabled && pending && recentAuth === "valid";
  const returnTo = `/consents/${consent.consent_id}`;
  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">Phase 7 · trusted approval</p>
        <h1 className="text-3xl font-bold">Yêu cầu {intent.intent_id}</h1>
        <p className="mt-2 text-slate-600">{intentStateCopy[intent.state]}</p>
      </div>

      {result === "approved" && (
        <p className="status-panel status-safe">Quyết định phê duyệt đã được Gateway ghi nhận.</p>
      )}
      {result === "denied" && (
        <p className="status-panel status-caution">Quyết định từ chối đã được Gateway ghi nhận.</p>
      )}
      {error === "expired" && (
        <p className="warning-panel">Phê duyệt đã hết hạn. Hãy xem preview mới.</p>
      )}
      {error === "conflict" && (
        <p className="warning-panel">
          Trạng thái phê duyệt đã thay đổi hoặc phiên bản không còn khớp. Trang không tự phát lại quyết định.
        </p>
      )}

      <section className="card space-y-4">
        <h2 className="text-xl font-bold">Thông tin bất biến từ Gateway</h2>
        <dl className="summary-grid">
          <div><dt>Hành động</dt><dd>{intent.action === "program_commit" ? "Commit create-only" : "Rollback checkpoint"}</dd></div>
          <div><dt>Tài liệu</dt><dd>{intent.document_id}</dd></div>
          <div><dt>Revision dự kiến</dt><dd>{intent.expected_document_revision}</dd></div>
          <div><dt>Program</dt><dd>{intent.program_id} · rev {intent.program_revision}</dd></div>
          <div><dt>Preview</dt><dd>{intent.preview_id}</dd></div>
          <div><dt>Rủi ro</dt><dd>{intent.risk_class}</dd></div>
          <div><dt>Mức bảo đảm</dt><dd>{intent.required_assurance}</dd></div>
          <div><dt>Hết hạn</dt><dd>{new Date(consent.expires_at).toLocaleString("vi-VN")}</dd></div>
          <div><dt>Intent digest</dt><dd><code className="digest">{intent.intent_digest}</code></dd></div>
        </dl>
      </section>

      <section className="card space-y-3">
        <h2 className="text-xl font-bold">Tác động đáng tin cậy</h2>
        <ul className="list-disc space-y-2 pl-5">
          {intent.trusted_effect_summary.map((item, index) => (
            <li key={`${item.kind}-${index}`}>{safeTrustedSummary(item.summary)} · {item.count}</li>
          ))}
        </ul>
        <p className="text-sm text-slate-600">
          Nội dung mô tả do model tạo không được dùng làm dữ kiện phê duyệt.
        </p>
      </section>

      {!approvalEnabled && (
        <p className="warning-panel">
          Chức năng phê duyệt recent-auth đang tắt. Không có đường bỏ qua.
        </p>
      )}
      {approvalEnabled && pending && recentAuth !== "valid" && (
        <section className="warning-panel space-y-3">
          <p>
            Cần xác thực lại trước khi quyết định. Thời gian đăng nhập từ trình duyệt không được tin cậy.
          </p>
          <a
            className="button primary"
            href={`/api/auth/login?recent=1&returnTo=${encodeURIComponent(returnTo)}`}
          >
            Xác thực lại an toàn
          </a>
        </section>
      )}
      {canDecide && (
        <section className="card space-y-3">
          <h2 className="text-xl font-bold">Quyết định</h2>
          <p>Quyết định chỉ áp dụng cho đúng digest, phiên bản và nonce đang lưu tại Gateway.</p>
          <div className="flex flex-wrap gap-3">
            <form method="post" action={`/api/bff/consents/${consent.consent_id}/approve`}>
              <input type="hidden" name="csrf" value={csrfToken} />
              <button className="primary" type="submit">Phê duyệt đúng yêu cầu này</button>
            </form>
            <form method="post" action={`/api/bff/consents/${consent.consent_id}/deny`}>
              <input type="hidden" name="csrf" value={csrfToken} />
              <button className="danger" type="submit">Từ chối yêu cầu này</button>
            </form>
          </div>
        </section>
      )}
      {intent.state === "released" && intent.released_job_id && (
        <p className="card">
          Đã phát hành. <Link href={`/jobs/${intent.released_job_id}`}>Xem tác vụ thực thi</Link>.
        </p>
      )}
    </section>
  );
}
