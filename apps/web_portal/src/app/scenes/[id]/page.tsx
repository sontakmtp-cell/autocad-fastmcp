import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import type { SceneItem, ScenePage, SceneSection } from "@/lib/contracts";
import { GatewayClient, GatewayError } from "@/lib/gateway-client";
import { getSession } from "@/lib/session";

const sections: SceneSection[] = [
  "nodes", "relations", "contours", "features", "issues", "evidence",
];
const typeFilters: Partial<Record<SceneSection, readonly string[]>> = {
  nodes: ["LINE", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE"],
  relations: [
    "connected_endpoint", "touch", "intersect", "overlap", "duplicate_geometry",
    "inside", "contains", "parallel", "perpendicular", "concentric", "aligned",
  ],
  features: [
    "part", "hole", "repeated_hole_pattern", "concentric_group", "slot",
    "centerline_candidate", "annotation_link",
  ],
  issues: [
    "duplicate_geometry", "degenerate_geometry", "open_contour", "unsupported_geometry",
    "truncated_geometry", "orphan_annotation", "ambiguous_annotation",
    "inconsistent_repeated_feature",
  ],
};

function itemIdentity(item: SceneItem): string {
  if ("node_id" in item) return item.node_id;
  if ("relation_id" in item) return item.relation_id;
  if ("contour_id" in item) return item.contour_id;
  if ("feature_id" in item) return item.feature_id;
  if ("issue_id" in item) return item.issue_id;
  return item.evidence_id;
}

function itemKind(item: SceneItem): string {
  if ("entity_type" in item) return item.entity_type;
  if ("relation_type" in item) return item.relation_type;
  if ("feature_type" in item) return item.feature_type;
  if ("code" in item) return `${item.severity} · ${item.code}`;
  if ("evidence_type" in item) return item.evidence_type;
  return item.closed ? "closed contour" : "open contour";
}

function sourceIds(item: SceneItem): string[] {
  if ("source_entity_id" in item) return [item.source_entity_id];
  if ("source_entity_ids" in item) return item.source_entity_ids;
  if ("source_node_ids" in item) return item.source_node_ids;
  return [];
}

function evidenceIds(item: SceneItem): string[] {
  return "evidence_ids" in item ? item.evidence_ids : [];
}

function confidenceValue(item: SceneItem): number | undefined {
  return "confidence" in item ? item.confidence : undefined;
}

function SceneItems({ page }: { page: ScenePage }) {
  return (
    <ul className="space-y-3">
      {page.items.map((item) => {
        const identity = itemIdentity(item);
        const evidence = evidenceIds(item);
        const sources = sourceIds(item);
        const itemConfidence = confidenceValue(item);
        return (
          <li className="rounded-lg border border-slate-200 p-4" id={identity} key={identity}>
            <p className="font-bold">{itemKind(item)}</p>
            <p className="digest mt-1 text-slate-600">{identity}</p>
            {itemConfidence !== undefined && (
              <p className="mt-2 text-sm">Confidence: {itemConfidence.toFixed(3)}</p>
            )}
            {sources.length > 0 && (
              <p className="mt-2 text-sm break-anywhere">
                Source: {sources.join(", ")}
              </p>
            )}
            {evidence.length > 0 && (
              <p className="mt-2 text-sm break-anywhere">
                Evidence: {evidence.join(", ")} ·{" "}
                <Link href="?section=evidence">mở evidence section</Link>
              </p>
            )}
            {"limitations" in item && item.limitations.length > 0 && (
              <p className="mt-2 text-sm text-amber-800">
                Limitations: {item.limitations.join(", ")}
              </p>
            )}
            {"write_authority" in item && (
              <p className="mt-2 text-sm font-bold text-slate-600">Write authority: false</p>
            )}
          </li>
        );
      })}
      {page.items.length === 0 && (
        <li className="text-slate-600">Không có evidence phù hợp bộ lọc.</li>
      )}
    </ul>
  );
}

export default async function SceneDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await getSession();
  if (!session) redirect("/login");

  const { id } = await params;
  const query = await searchParams;
  const requestedSection = typeof query.section === "string" ? query.section : "";
  const section: SceneSection = sections.includes(requestedSection as SceneSection)
    ? requestedSection as SceneSection
    : "features";
  const requestedType = typeof query.type === "string" ? query.type : undefined;
  const selectedType = typeFilters[section]?.includes(requestedType ?? "")
    ? requestedType
    : undefined;
  const requestedConfidence = typeof query.confidence === "string"
    ? Number(query.confidence)
    : undefined;
  const confidenceMin = requestedConfidence !== undefined
    && Number.isFinite(requestedConfidence)
    && requestedConfidence >= 0
    && requestedConfidence <= 1
    && ["relations", "features", "issues"].includes(section)
    ? requestedConfidence
    : undefined;

  let scene;
  let page;
  try {
    const client = new GatewayClient(session);
    [scene, page] = await Promise.all([
      client.getScene(id),
      client.queryScene(id, {
        section,
        entityTypes: section === "nodes" && selectedType ? [selectedType] : undefined,
        relationTypes: section === "relations" && selectedType ? [selectedType] : undefined,
        featureTypes: section === "features" && selectedType ? [selectedType] : undefined,
        issueCodes: section === "issues" && selectedType ? [selectedType] : undefined,
        confidenceMin,
        limit: 100,
      }),
    ]);
  } catch (error) {
    if (error instanceof GatewayError && error.status === 404) notFound();
    throw error;
  }

  return (
    <section className="card space-y-6">
      <div>
        <Link href="/scenes">← Drawing scenes</Link>
        <p className="eyebrow mt-4">Phase 10 · read-only evidence</p>
        <h1 className="text-3xl font-bold">{scene.document_id}</h1>
        <p className="mt-2 text-slate-600 break-anywhere">{scene.scene_id}</p>
      </div>

      <p className={scene.complete ? "status-panel status-safe" : "status-panel status-caution"}>
        {scene.complete
          ? "Scene complete trong các budget đã công bố."
          : `Scene partial: ${scene.truncation_reasons.join(", ")}; ${scene.counts.omitted} mục bị bỏ.`}
      </p>

      <dl className="summary-grid">
        <div><dt>Document revision</dt><dd className="break-anywhere">{scene.document_revision}</dd></div>
        <div><dt>Snapshot</dt><dd>{scene.source_snapshot_id}</dd></div>
        <div><dt>Engine</dt><dd>{scene.engine_version}</dd></div>
        <div><dt>Profile</dt><dd>{scene.profile_id}</dd></div>
        <div><dt>Projection</dt><dd>{scene.projection_version}</dd></div>
        <div><dt>Source available</dt><dd>{scene.source_snapshot_available ? "yes" : "no"}</dd></div>
        <div><dt>Scene digest</dt><dd className="digest">{scene.scene_digest}</dd></div>
        <div><dt>Source digest</dt><dd className="digest">{scene.source_digest}</dd></div>
      </dl>

      {scene.warnings.length > 0 && (
        <p className="warning-panel">Warnings: {scene.warnings.join(", ")}</p>
      )}

      <nav aria-label="Scene sections" className="flex flex-wrap gap-3">
        {sections.map((name) => (
          <Link
            className={name === section ? "button primary" : "button secondary"}
            href={`?section=${name}`}
            key={name}
          >
            {name} ({scene.counts[name]})
          </Link>
        ))}
      </nav>

      {(typeFilters[section] || ["relations", "features", "issues"].includes(section)) && (
        <form className="rounded-lg border border-slate-200 p-4" method="get">
          <input name="section" type="hidden" value={section} />
          <div className="flex flex-wrap items-end gap-3">
            {typeFilters[section] && (
              <label>
                <span className="block text-sm font-bold">Type</span>
                <select className="mt-1 rounded border p-2" defaultValue={selectedType ?? ""} name="type">
                  <option value="">All</option>
                  {typeFilters[section]?.map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </label>
            )}
            {["relations", "features", "issues"].includes(section) && (
              <label>
                <span className="block text-sm font-bold">Minimum confidence</span>
                <select className="mt-1 rounded border p-2" defaultValue={confidenceMin ?? ""} name="confidence">
                  <option value="">All</option>
                  <option value="0.5">0.5</option>
                  <option value="0.8">0.8</option>
                  <option value="0.95">0.95</option>
                </select>
              </label>
            )}
            <button className="secondary" type="submit">Filter evidence</button>
          </div>
        </form>
      )}

      <div>
        <h2 className="mb-3 text-xl font-bold">
          {section} · {page.total} total
        </h2>
        <SceneItems page={page} />
        {page.next_cursor && (
          <p className="warning-panel mt-4">
            Còn dữ liệu sau trang này. Cursor chỉ được Gateway cấp và không được chỉnh sửa ở UI.
          </p>
        )}
      </div>
    </section>
  );
}
