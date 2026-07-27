import Link from "next/link";
import { Phase6Disabled } from "@/components/Phase6Disabled";
import { Phase6Status } from "@/components/Phase6Status";
import { RecordSummary } from "@/components/RecordSummary";
import { GatewayClient } from "@/lib/gateway-client";
import { phase6NotFound, phase6PageContext } from "@/lib/phase6-page";

export default async function ReceiptPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { session, state } = await phase6PageContext(`/receipts/${id}`);
  if (!state.phase6Enabled) return <Phase6Disabled state={state} />;
  let receipt;
  try {
    receipt = await new GatewayClient(session).getReceipt(id);
  } catch (error) {
    phase6NotFound(error);
  }
  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">Commit receipt · effect đã ghi nhận</p>
        <h1 className="text-3xl font-bold">Receipt {receipt.receipt_id}</h1>
        <p className="mt-2 text-slate-600">Bản ghi durable từ đúng preview và execution binding.</p>
      </div>
      <Phase6Status state={state} />
      <section className="card">
        <dl className="summary-grid">
          <div><dt>Program</dt><dd><Link href={`/programs/${receipt.program_id}/revisions/${receipt.program_revision}`}>{receipt.program_id} · rev {receipt.program_revision}</Link></dd></div>
          <div><dt>Preview</dt><dd><Link href={`/previews/${receipt.preview_id}`}>{receipt.preview_id}</Link></dd></div>
          <div><dt>Job</dt><dd><Link href={`/jobs/${receipt.job_id}`}>{receipt.job_id}</Link></dd></div>
          <div><dt>Tài liệu</dt><dd>{receipt.document_id}</dd></div>
          <div><dt>Revision trước</dt><dd>{receipt.document_revision_before}</dd></div>
          <div><dt>Revision sau</dt><dd>{receipt.document_revision_after}</dd></div>
          <div><dt>Runtime</dt><dd>{receipt.runtime_id}</dd></div>
          <div><dt>Binding digest</dt><dd><code className="digest">{receipt.binding_digest}</code></dd></div>
          <div><dt>Package hash</dt><dd><code className="digest">{receipt.package_hash}</code></dd></div>
          <div><dt>Capability hash</dt><dd><code className="digest">{receipt.capability_manifest_hash}</code></dd></div>
          <div><dt>Registry hash</dt><dd><code className="digest">{receipt.operation_registry_hash}</code></dd></div>
          <div><dt>Policy</dt><dd>{receipt.policy_version}</dd></div>
        </dl>
      </section>
      <RecordSummary title="Kết quả effect" value={receipt.effect_summary} />
      <RecordSummary title="Durable receipt" value={receipt.durable_receipt} />
    </section>
  );
}
