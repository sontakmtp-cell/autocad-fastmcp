import Link from "next/link";
import { GatewayClient } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";
import { redirect } from "next/navigation";

export default async function WorkflowsPage() {
  const session = await getSession();
  if (!session) redirect("/login?returnTo=%2Fworkflows");
  let runs = [] as Awaited<ReturnType<GatewayClient["listWorkflows"]>>;
  try { runs = await new GatewayClient(session).listWorkflows(); } catch { /* default-off or no runs is safe */ }
  return <section className="card space-y-5"><p className="eyebrow">Phase 9 workflow</p><h1 className="text-3xl font-bold">Workflow runs</h1><p className="text-slate-600">Approval vẫn dùng luồng consent hiện có; trang này chỉ hiển thị trạng thái và liên kết audit.</p><ul className="space-y-3">{runs.map((run) => <li key={run.run_id} className="border rounded p-3"><Link href={`/workflows/${encodeURIComponent(run.run_id)}`}>{run.skill_id} {run.skill_version}</Link><div className="text-sm text-slate-600">{run.state} · step {run.current_step_id ?? "—"}</div></li>)}{runs.length === 0 && <li className="text-slate-600">Chưa có workflow khả dụng hoặc Phase 9 đang tắt.</li>}</ul></section>;
}
