import { Phase6Disabled } from "@/components/Phase6Disabled";
import { Phase6Status } from "@/components/Phase6Status";
import { Phase6Warning } from "@/components/Phase6Warning";
import { RecordSummary } from "@/components/RecordSummary";
import { GatewayClient } from "@/lib/gateway-client";
import { phase6NotFound, phase6PageContext } from "@/lib/phase6-page";

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { session, state } = await phase6PageContext(`/jobs/${id}`);
  if (!state.phase6Enabled) return <Phase6Disabled state={state} />;
  let job;
  try {
    job = await new GatewayClient(session).getJob(id);
  } catch (error) {
    phase6NotFound(error);
  }
  return (
    <section className="space-y-5">
      <div>
        <p className="eyebrow">Activity · durable job</p>
        <h1 className="text-3xl font-bold">Job {job.job_id}</h1>
        <p className="mt-2 text-slate-600">{job.kind} · {job.effect_class} · {job.state}</p>
      </div>
      <Phase6Status state={state} />
      {job.error_code && <Phase6Warning code={job.error_code} />}
      {job.state === "outcome_unknown" && job.error_code !== "outcome_unknown" && (
        <Phase6Warning code="outcome_unknown" />
      )}
      {job.state === "needs_attention" && !job.error_code && <Phase6Warning code="needs_attention" />}
      <section className="card">
        <dl className="summary-grid">
          <div><dt>Thiết bị</dt><dd>{job.device_id}</dd></div>
          <div><dt>Loại</dt><dd>{job.kind}</dd></div>
          <div><dt>Effect class</dt><dd>{job.effect_class}</dd></div>
          <div><dt>Trạng thái</dt><dd>{job.state}</dd></div>
          <div><dt>Tạo lúc</dt><dd>{new Date(job.created_at).toLocaleString("vi-VN")}</dd></div>
          <div><dt>Cập nhật</dt><dd>{new Date(job.updated_at).toLocaleString("vi-VN")}</dd></div>
        </dl>
      </section>
      {job.progress && <RecordSummary title="Tiến độ" value={job.progress} />}
      {job.result && <RecordSummary title="Kết quả" value={job.result} />}
      {["outcome_unknown", "needs_attention"].includes(job.state) && (
        <p className="card">
          Không có nút chạy lại write. Hãy làm mới evidence hoặc dùng support ID từ Desktop Agent.
        </p>
      )}
    </section>
  );
}
