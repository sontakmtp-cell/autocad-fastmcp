import Link from "next/link";
import { BindingSummary } from "@/components/BindingSummary";
import { Phase6Disabled } from "@/components/Phase6Disabled";
import { Phase6Status } from "@/components/Phase6Status";
import { Phase6Warning } from "@/components/Phase6Warning";
import { RecordSummary } from "@/components/RecordSummary";
import { GatewayClient } from "@/lib/gateway-client";
import { phase6NotFound, phase6PageContext } from "@/lib/phase6-page";

export default async function PreviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { session, state } = await phase6PageContext(`/previews/${id}`);
  if (!state.phase6Enabled) return <Phase6Disabled state={state} />;

  let preview;
  try {
    preview = await new GatewayClient(session).getPreview(id);
  } catch (error) {
    phase6NotFound(error);
  }
  const expired = Date.parse(preview.expires_at) <= Date.now();
  const binding = {
    runtime_id: preview.runtime_id,
    runtime_role: preview.runtime_role,
    host_family: preview.host_family,
    host_version: preview.host_version,
    package_id: preview.package_id,
    package_version: preview.package_version,
    package_hash: preview.package_hash,
    capability_manifest_hash: preview.capability_manifest_hash,
    operation_registry_hash: preview.operation_registry_hash,
    registry_version: preview.registry_version,
    policy_version: preview.policy_version,
  };

  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">Preview · giao dịch thử đã abort</p>
        <h1 className="text-3xl font-bold">Preview {preview.preview_id}</h1>
        <p className="mt-2 text-slate-600">
          DWG chưa thay đổi. Preview thành công không có nghĩa là đã được phê duyệt.
        </p>
      </div>
      <Phase6Status state={state} />
      {preview.invalidated_reason && <Phase6Warning code={preview.invalidated_reason} />}
      {!preview.invalidated_reason && expired && <Phase6Warning code="preview_expired" />}
      <section className="card">
        <dl className="summary-grid">
          <div><dt>Program</dt><dd><Link href={`/programs/${preview.program_id}/revisions/${preview.program_revision}`}>{preview.program_id} · rev {preview.program_revision}</Link></dd></div>
          <div><dt>Job</dt><dd><Link href={`/jobs/${preview.job_id}`}>{preview.job_id}</Link></dd></div>
          <div><dt>Operations dự kiến</dt><dd>{preview.planned_operation_count}</dd></div>
          <div><dt>Entities dự kiến</dt><dd>{preview.planned_entity_count}</dd></div>
          <div><dt>Layers dự kiến</dt><dd>{preview.planned_layer_count}</dd></div>
          <div><dt>Hết hạn</dt><dd>{new Date(preview.expires_at).toLocaleString("vi-VN")}</dd></div>
          <div><dt>Binding digest</dt><dd><code className="digest">{preview.binding_digest}</code></dd></div>
          <div><dt>Execution digest</dt><dd><code className="digest">{preview.execution_digest}</code></dd></div>
        </dl>
      </section>
      <BindingSummary binding={binding} />
      <RecordSummary title="Kết quả kiểm tra preview" value={preview.validation} />
    </section>
  );
}
