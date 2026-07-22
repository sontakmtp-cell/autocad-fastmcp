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
    PAUSED = "paused_by_user"
    INCOMPATIBLE = "incompatible"


class AgentIntent(StrEnum):
    RETRY = "retry"
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


STATE_COPY: dict[RuntimeState, tuple[str, str]] = {
    RuntimeState.OFFLINE: ("Mất kết nối máy chủ", "Đang thử kết nối lại…"),
    RuntimeState.CONNECTING: ("Đang kết nối", "Agent đang kết nối tới máy chủ."),
    RuntimeState.READY: ("Sẵn sàng", "AutoCAD và bản vẽ đã sẵn sàng."),
    RuntimeState.BUSY_USER: ("AutoCAD đang được sử dụng", "Hãy hoàn tất lệnh trong AutoCAD."),
    RuntimeState.BUSY_REMOTE: ("ChatGPT đang thực hiện tác vụ", "Không đóng Agent khi tác vụ đang chạy."),
    RuntimeState.MODAL: ("AutoCAD đang chờ hộp thoại", "Hãy xử lý hộp thoại trong AutoCAD rồi thử lại."),
    RuntimeState.AUTOCAD_CLOSED: ("AutoCAD chưa mở", "Hãy mở AutoCAD rồi bấm Thử lại."),
    RuntimeState.NO_DOCUMENT: ("Chưa mở bản vẽ", "Hãy mở một bản vẽ trong AutoCAD."),
    RuntimeState.PAUSED: ("Đã tạm dừng", "Mọi tác vụ từ xa mới đang bị chặn."),
    RuntimeState.INCOMPATIBLE: ("Phiên bản không tương thích", "Hãy kiểm tra package hoặc cập nhật Agent."),
}
