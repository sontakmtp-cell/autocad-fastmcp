function safeDisplay(value: unknown): string {
  if (typeof value === "string") {
    return value.slice(0, 512);
  }
  if (typeof value === "number" || typeof value === "boolean" || value === null) {
    return String(value);
  }
  const encoded = JSON.stringify(value);
  return encoded ? encoded.slice(0, 1_000) : "—";
}

export function RecordSummary({
  title,
  value,
}: {
  title: string;
  value: Record<string, unknown>;
}) {
  const entries = Object.entries(value).slice(0, 32);
  return (
    <section className="card space-y-3">
      <h2 className="text-xl font-bold">{title}</h2>
      {entries.length === 0 ? (
        <p className="text-slate-600">Không có chi tiết bổ sung.</p>
      ) : (
        <dl className="summary-grid">
          {entries.map(([key, item]) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd className="break-anywhere">{safeDisplay(item)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
