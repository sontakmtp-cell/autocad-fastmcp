import type { ExecutionBinding } from "@/lib/contracts";

type BindingLike = ExecutionBinding | {
  runtime_id: string;
  runtime_role: string;
  host_family: string;
  host_version: string;
  package_id: string;
  package_version: string;
  package_hash: string;
  capability_manifest_hash: string;
  operation_registry_hash: string;
  registry_version: string;
  policy_version: string;
};

function Digest({ value }: { value: string }) {
  return <code className="digest" title={value}>{value}</code>;
}

export function BindingSummary({ binding }: { binding: BindingLike }) {
  return (
    <section className="card space-y-3" aria-labelledby="binding-heading">
      <div>
        <p className="eyebrow">Ràng buộc thực thi chính xác</p>
        <h2 id="binding-heading" className="text-xl font-bold">
          Runtime, package và capability
        </h2>
      </div>
      <dl className="summary-grid">
        <div><dt>Runtime</dt><dd>{binding.runtime_id} · {binding.runtime_role}</dd></div>
        <div><dt>Host</dt><dd>{binding.host_family} · {binding.host_version}</dd></div>
        <div><dt>Package</dt><dd>{binding.package_id} · {binding.package_version}</dd></div>
        <div><dt>Registry</dt><dd>{binding.registry_version}</dd></div>
        <div><dt>Policy</dt><dd>{binding.policy_version}</dd></div>
        <div><dt>Package hash</dt><dd><Digest value={binding.package_hash} /></dd></div>
        <div><dt>Capability hash</dt><dd><Digest value={binding.capability_manifest_hash} /></dd></div>
        <div><dt>Registry hash</dt><dd><Digest value={binding.operation_registry_hash} /></dd></div>
      </dl>
    </section>
  );
}
