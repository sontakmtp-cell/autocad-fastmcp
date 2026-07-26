import Link from "next/link";
import type { Device } from "@/lib/contracts";
import { RuntimeBadge } from "./RuntimeBadge";

export function DeviceCard({ device }: { device: Device }) {
  return (
    <article className="card space-y-3" data-testid={`device-${device.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 className="text-xl font-bold">{device.name}</h2>
        <span>{device.connected ? "● Đang kết nối" : "○ Ngoại tuyến"}</span>
      </div>
      <RuntimeBadge runtime={device.runtime} />
      <p className="text-sm text-slate-600">
        {device.is_default ? "Thiết bị mặc định" : "Thiết bị phụ"}
      </p>
      <Link href={`/devices/${device.id}`}>Xem chi tiết và thu hồi</Link>
    </article>
  );
}
