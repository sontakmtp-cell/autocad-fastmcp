import Link from "next/link";
import { redirect } from "next/navigation";
import { GatewayClient } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";

export default async function ScenesPage() {
  const session = await getSession();
  if (!session) redirect("/login?returnTo=%2Fscenes");

  let scenes = [] as Awaited<ReturnType<GatewayClient["listScenes"]>>;
  try {
    scenes = await new GatewayClient(session).listScenes();
  } catch {
    // Phase 10 is default-off; an unavailable list must not expose Gateway details.
  }

  return (
    <section className="card space-y-5">
      <div>
        <p className="eyebrow">Phase 10 · read-only</p>
        <h1 className="text-3xl font-bold">Drawing scenes</h1>
        <p className="mt-2 text-slate-600">
          Scene là bằng chứng phân tích có phiên bản. Trang này không sửa bản vẽ,
          không chạy lại phân tích và không cấp quyền ghi.
        </p>
      </div>
      <ul className="space-y-3">
        {scenes.map((scene) => (
          <li className="rounded-lg border border-slate-200 p-4" key={scene.scene_id}>
            <Link className="font-bold" href={`/scenes/${encodeURIComponent(scene.scene_id)}`}>
              {scene.document_id}
            </Link>
            <p className="mt-1 text-sm text-slate-600 break-anywhere">
              {scene.scene_id} · revision {scene.document_revision}
            </p>
            <p className="mt-2 text-sm">
              {scene.counts.nodes} nodes · {scene.counts.relations} relations ·{" "}
              {scene.counts.features} features · {scene.counts.issues} issues
            </p>
            <p className={scene.complete ? "status-safe mt-2 inline-block rounded px-2 py-1 text-sm" : "status-caution mt-2 inline-block rounded px-2 py-1 text-sm"}>
              {scene.complete ? "Complete" : `Partial · ${scene.counts.omitted} omitted`}
            </p>
          </li>
        ))}
        {scenes.length === 0 && (
          <li className="text-slate-600">
            Chưa có scene khả dụng hoặc Phase 10 đang tắt.
          </li>
        )}
      </ul>
    </section>
  );
}
