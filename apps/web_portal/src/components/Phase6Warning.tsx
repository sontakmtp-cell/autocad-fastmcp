const warningCopy: Record<string, string> = {
  capability_missing: "Tác vụ không được hỗ trợ trên runtime hoặc phiên bản hiện tại.",
  runtime_changed: "Môi trường thực thi đã thay đổi. Hãy tạo preview mới.",
  document_changed: "Bản vẽ đã thay đổi sau preview. Hãy tạo preview mới.",
  preview_expired: "Preview đã hết hạn. Hãy tạo preview mới.",
  execution_binding_changed: "Runtime, package hoặc capability đã đổi. Preview cũ không còn hiệu lực.",
  write_lock_disabled: "Khóa write cục bộ đang tắt. Tác vụ chưa được phép chạy.",
  hard_pause: "Desktop Agent đang hard pause. Tác vụ mới bị chặn.",
  outcome_unknown: "Chưa thể xác định thao tác đã hoàn tất hay chưa. Hệ thống sẽ không tự chạy lại.",
  needs_attention: "Tác vụ cần được kiểm tra bằng evidence và support ID trước khi có hành động tiếp theo.",
};

export function Phase6Warning({
  code,
  fallback,
}: {
  code: string;
  fallback?: string;
}) {
  return (
    <aside className="warning-panel" role="status">
      <strong>Cần chú ý: {code}</strong>
      <p className="mt-1 text-sm">
        {warningCopy[code] ?? fallback ?? "Trạng thái này cần được kiểm tra trước khi tiếp tục."}
      </p>
    </aside>
  );
}
