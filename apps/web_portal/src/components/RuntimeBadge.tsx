import type { Device } from "@/lib/contracts";

export function RuntimeBadge({ runtime }: { runtime: Device["runtime"] }) {
  if (!runtime) {
    return <span className="rounded bg-slate-100 px-2 py-1 text-sm">Chưa có dữ liệu runtime</span>;
  }

  const role = {
    primary: "Runtime chính",
    compatibility: "Tương thích LT",
    fallback: "Dự phòng / suy giảm",
    unsupported: "Không hỗ trợ",
  }[runtime.role];
  return (
    <span className="rounded bg-slate-100 px-2 py-1 text-sm">
      {runtime.label} · {role} · {runtime.health}
    </span>
  );
}
