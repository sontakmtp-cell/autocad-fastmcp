import Link from "next/link";
import { GatewayClient } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";
import { notFound, redirect } from "next/navigation";

export default async function WorkflowDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await getSession(); if (!session) redirect("/login");
  try {
    const { id } = await params; const detail = await new GatewayClient(session).getWorkflow(id);
    const r = detail.run;
    return <section className="card space-y-5"><Link href="/workflows">← Workflow runs</Link><h1 className="text-2xl font-bold">{r.skill_id} {r.skill_version}</h1><dl className="summary-grid"><div><dt>State</dt><dd>{r.state}</dd></div><div><dt>Current step</dt><dd>{r.current_step_id ?? "—"}</dd></div><div><dt>Device</dt><dd>{r.device_id}</dd></div></dl><h2 className="text-xl font-bold">Timeline</h2><ol className="space-y-2">{detail.events.map((e) => <li key={e.sequence} className="border-l-2 border-slate-300 pl-3"><strong>{e.event_type}</strong><div className="text-sm text-slate-600">{e.created_at}</div></li>)}</ol><p className="warning-panel">Nếu run chờ approval, mở liên kết intent/consent đã có trong Portal. Không có nút approve riêng cho workflow.</p></section>;
  } catch { notFound(); }
}
