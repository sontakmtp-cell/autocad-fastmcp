"""Immutable user-facing Agent state and typed UI intents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RuntimeState(StrEnum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    READY = "online_idle"
    BUSY_USER = "online_busy_user"
    BUSY_REMOTE = "online_busy_remote"
    MODAL = "modal_dialog"
    AUTOCAD_CLOSED = "autocad_closed"
    NO_DOCUMENT = "no_document"
    HOST_CONNECTING = "host_connecting"
    PLUGIN_REQUIRED = "plugin_required"
    HOST_NOT_LOADED = "host_not_loaded"
    VERSION_MISMATCH = "runtime_version_mismatch"
    DEGRADED = "degraded_compatibility"
    CAPABILITY_MISSING = "capability_missing"
    RUNTIME_CHANGED = "runtime_changed"
    PAUSED = "paused_by_user"
    UPDATE_REQUIRED = "update_required"
    OUTCOME_UNKNOWN = "outcome_unknown"
    INCOMPATIBLE = "incompatible"


class AgentIntent(StrEnum):
    RETRY = "retry"
    RETRY_RUNTIME_PROBE = "retry_runtime_probe"
    PAUSE = "pause"
    RESUME = "resume"
    EXPORT_DIAGNOSTICS = "export_diagnostics"
    EXIT = "exit"


@dataclass(frozen=True)
class AgentViewState:
    device_name: str
    runtime_state: RuntimeState = RuntimeState.OFFLINE
    server_connected: bool = False
    autocad_state: str = "Chưa kiểm tra"
    document_name: str | None = None
    current_task: str | None = None
    agent_version: str = "0.1.0"
    package_version: str = "3.3-c1"
    paused: bool = False
    support_code: str | None = None
    product: str | None = None
    edition: str | None = None
    release_year: int | None = None
    series: str | None = None
    vertical: str | None = None
    runtime_id: str | None = None
    runtime_role: str | None = None
    runtime_label: str = "Chưa kiểm tra"
    degradation_reason: str | None = None
    host_family: str | None = None
    host_version: str | None = None
    host_package_version: str | None = None
    host_package_hash: str | None = None
    host_handshake_state: str | None = None
    capability_manifest_hash: str | None = None
    registry_version: str | None = None
    managed_host_enabled: bool = False
    full_compat_fallback_enabled: bool = False


STATE_COPY: dict[RuntimeState, tuple[str, str]] = {
    RuntimeState.OFFLINE: ("Mất kết nối máy chủ", "Đang thử kết nối lại…"),
    RuntimeState.CONNECTING: ("Đang kết nối", "Agent đang kết nối tới máy chủ."),
    RuntimeState.READY: ("Sẵn sàng", "AutoCAD và bản vẽ đã sẵn sàng."),
    RuntimeState.BUSY_USER: ("AutoCAD đang được sử dụng", "Hãy hoàn tất lệnh trong AutoCAD."),
    RuntimeState.BUSY_REMOTE: ("ChatGPT đang thực hiện tác vụ", "Không đóng Agent khi tác vụ đang chạy."),
    RuntimeState.MODAL: ("AutoCAD đang chờ hộp thoại", "Hãy xử lý hộp thoại trong AutoCAD rồi thử lại."),
    RuntimeState.AUTOCAD_CLOSED: ("AutoCAD chưa mở", "Hãy mở AutoCAD rồi bấm Thử lại."),
    RuntimeState.NO_DOCUMENT: ("Chưa mở bản vẽ", "Hãy mở một bản vẽ trong AutoCAD."),
    RuntimeState.HOST_CONNECTING: (
        "Đang kết nối thành phần AutoCAD",
        "Agent đang thử kết nối lại trong giới hạn an toàn.",
    ),
    RuntimeState.PLUGIN_REQUIRED: (
        "Cần cài thành phần AutoCAD",
        "AutoCAD Full chưa tải được thành phần Managed .NET.",
    ),
    RuntimeState.HOST_NOT_LOADED: (
        "Thành phần AutoCAD chưa được tải",
        "Hãy kiểm tra trusted path và tải lại thành phần đã ký.",
    ),
    RuntimeState.VERSION_MISMATCH: (
        "Thành phần không tương thích",
        "Agent và thành phần AutoCAD cần được cập nhật đúng cặp.",
    ),
    RuntimeState.DEGRADED: (
        "Khả năng đang bị giới hạn",
        "Chỉ tác vụ đọc được policy và capability cho phép mới chạy.",
    ),
    RuntimeState.CAPABILITY_MISSING: (
        "Tác vụ không được hỗ trợ trên máy này",
        "Agent không thay bằng một thao tác gần giống.",
    ),
    RuntimeState.RUNTIME_CHANGED: (
        "Môi trường thực thi đã thay đổi",
        "Preview cũ không còn hiệu lực.",
    ),
    RuntimeState.PAUSED: ("Đã tạm dừng", "Mọi tác vụ từ xa mới đang bị chặn."),
    RuntimeState.UPDATE_REQUIRED: (
        "Cần cập nhật",
        "Hãy cập nhật thành phần trước khi chạy tác vụ mới.",
    ),
    RuntimeState.OUTCOME_UNKNOWN: (
        "Cần kiểm tra bản vẽ",
        "Không tự thử lại thao tác chỉnh sửa.",
    ),
    RuntimeState.INCOMPATIBLE: ("Phiên bản không tương thích", "Hãy kiểm tra package hoặc cập nhật Agent."),
}


def runtime_user_label(state: AgentViewState) -> str:
    if state.runtime_id == "managed_dotnet" and state.runtime_role == "primary":
        return "Hiệu năng đầy đủ (.NET)"
    if state.runtime_id == "autolisp_file_ipc" and state.edition == "lt":
        return "Tương thích AutoCAD LT"
    if state.runtime_id == "autolisp_file_ipc" and (
        state.runtime_role == "compatibility_fallback" or state.degradation_reason
    ):
        return "Chế độ tương thích giới hạn"
    if state.runtime_state == RuntimeState.VERSION_MISMATCH:
        return "Thành phần AutoCAD không tương thích"
    if state.runtime_state in {RuntimeState.PLUGIN_REQUIRED, RuntimeState.HOST_NOT_LOADED}:
        return "Chưa sẵn sàng đầy đủ"
    if state.runtime_state == RuntimeState.DEGRADED:
        return "Đang chạy với khả năng giới hạn"
    return state.runtime_label
