import type { Phase6UiState } from "@/lib/env";

export function Phase6Status({ state }: { state: Phase6UiState }) {
  const writeLabel = state.managedWriteEnabled
    ? "Gateway xác nhận pilot write đang được hiển thị"
    : "Write đang tắt";
  const detail = !state.gatewayStateAvailable
    ? "Không đọc được trạng thái phát hành từ Gateway; Portal giữ fail-closed."
    : !state.phase6Enabled
    ? "Giao diện Phase 6 chưa được bật. Portal sẽ không tải dữ liệu CAD Program."
    : state.killSwitchActive
      ? "Kill switch đang hoạt động; mọi điều khiển write công khai phải giữ fail-closed."
      : state.managedWriteEnabled
        ? "Chỉ create-only trên Managed .NET R25 đã allowlist; Gateway và Agent vẫn là nơi enforcement, AutoCAD LT vẫn read-only."
        : "Giao diện đọc được bật nhưng điều khiển write chưa được phát hành.";

  return (
    <aside
      className={`status-panel ${state.managedWriteEnabled ? "status-caution" : "status-safe"}`}
      aria-label="Trạng thái phát hành Phase 6"
    >
      <strong>{writeLabel}</strong>
      <p className="mt-1 text-sm">{detail}</p>
    </aside>
  );
}
