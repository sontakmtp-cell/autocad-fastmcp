import Link from "next/link";
import { Phase6Disabled } from "@/components/Phase6Disabled";
import { Phase6Status } from "@/components/Phase6Status";
import { RecordSummary } from "@/components/RecordSummary";
import { GatewayClient } from "@/lib/gateway-client";
import { phase6NotFound, phase6PageContext } from "@/lib/phase6-page";

export default async function ValidationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { session, state } = await phase6PageContext(`/validations/${id}`);
  if (!state.phase6Enabled) return <Phase6Disabled state={state} />;
  let validation;
  try {
    validation = await new GatewayClient(session).getValidation(id);
  } catch (error) {
    phase6NotFound(error);
  }
  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">Xác thực read-only</p>
        <h1 className="text-3xl font-bold">Validation {validation.validation_id}</h1>
        <p className="mt-2 text-slate-600">
          Kết quả: <strong>{validation.passed ? "Đạt" : "Không đạt"}</strong>
        </p>
      </div>
      <Phase6Status state={state} />
      <section className="card">
        <dl className="summary-grid">
          <div><dt>Program</dt><dd><Link href={`/programs/${validation.program_id}/revisions/${validation.program_revision}`}>{validation.program_id} · rev {validation.program_revision}</Link></dd></div>
          <div><dt>Receipt</dt><dd><Link href={`/receipts/${validation.receipt_id}`}>{validation.receipt_id}</Link></dd></div>
          <div><dt>Job</dt><dd><Link href={`/jobs/${validation.job_id}`}>{validation.job_id}</Link></dd></div>
          <div><dt>Document revision</dt><dd>{validation.document_revision}</dd></div>
          <div><dt>Binding digest</dt><dd><code className="digest">{validation.binding_digest}</code></dd></div>
          <div><dt>Execution digest</dt><dd><code className="digest">{validation.execution_digest}</code></dd></div>
        </dl>
      </section>
      <RecordSummary title="Báo cáo validation" value={validation.report} />
    </section>
  );
}
