function safeDisplay(key: string, value: unknown): string {
  if (/(?:token|secret|private.?key|pipe|credential|password|full.?path)/i.test(key)) {
    return "Đã ẩn vì bảo mật";
  }
  if (typeof value === "string") {
    if (/^(?:[A-Za-z]:\\|\\\\)/.test(value)) {
      return value.split(/[\\/]/).at(-1)?.slice(0, 255) ?? "Đã ẩn đường dẫn";
    }
    return value.slice(0, 512);
  }
  if (typeof value === "number" || typeof value === "boolean" || value === null) {
    return String(value);
  }
  const encoded = JSON.stringify(redactNested(value));
  return encoded ? encoded.slice(0, 1_000) : "—";
}

function redactNested(value: unknown, depth = 0): unknown {
  if (depth > 3) return "…";
  if (typeof value === "string") {
    if (/^(?:[A-Za-z]:\\|\\\\)/.test(value)) {
      return value.split(/[\\/]/).at(-1)?.slice(0, 255) ?? "Đã ẩn đường dẫn";
    }
    return value.slice(0, 512);
  }
  if (Array.isArray(value)) {
    return value.slice(0, 32).map((item) => redactNested(item, depth + 1));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).slice(0, 32).map(([key, item]) => [
        key,
        /(?:token|secret|private.?key|pipe|credential|password|full.?path)/i.test(key)
          ? "Đã ẩn vì bảo mật"
          : redactNested(item, depth + 1),
      ]),
    );
  }
  return value;
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
              <dd className="break-anywhere">{safeDisplay(key, item)}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
