import Link from "next/link";
import { BindingSummary } from "@/components/BindingSummary";
import { Phase6Disabled } from "@/components/Phase6Disabled";
import { Phase6Status } from "@/components/Phase6Status";
import { Phase6Warning } from "@/components/Phase6Warning";
import { GatewayClient } from "@/lib/gateway-client";
import { phase6NotFound, phase6PageContext } from "@/lib/phase6-page";

export default async function ProgramPage({
  params,
}: {
  params: Promise<{ id: string; revision: string }>;
}) {
  const { id, revision: rawRevision } = await params;
  const returnTo = `/programs/${id}/revisions/${rawRevision}`;
  const { session, state } = await phase6PageContext(returnTo);
  if (!state.phase6Enabled) return <Phase6Disabled state={state} />;

  let program;
  try {
    program = await new GatewayClient(session).getProgram(id, Number(rawRevision));
  } catch (error) {
    phase6NotFound(error);
  }

  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">Đã chuẩn bị · chưa sửa bản vẽ</p>
        <h1 className="text-3xl font-bold">CAD Program {program.program_id}</h1>
        <p className="mt-2 text-slate-600">
          Revision {program.program_revision} · {program.schema_version} · rủi ro {program.risk_class}
        </p>
      </div>
      <Phase6Status state={state} />
      {program.missing_capabilities.map((capability) => (
        <Phase6Warning code="capability_missing" fallback={`Thiếu capability: ${capability}`} key={capability} />
      ))}
      <section className="card">
        <dl className="summary-grid">
          <div><dt>Thiết bị</dt><dd><Link href={`/devices/${program.device_id}`}>{program.device_id}</Link></dd></div>
          <div><dt>Tài liệu</dt><dd>{program.document_id}</dd></div>
          <div><dt>Revision mong đợi</dt><dd>{program.expected_document_revision}</dd></div>
          <div><dt>Source snapshot</dt><dd>{program.source_snapshot_id}</dd></div>
          <div><dt>Program digest</dt><dd><code className="digest">{program.program_digest}</code></dd></div>
          <div><dt>Tạo lúc</dt><dd>{new Date(program.created_at).toLocaleString("vi-VN")}</dd></div>
        </dl>
      </section>
      <BindingSummary binding={program.pins} />
    </section>
  );
}
