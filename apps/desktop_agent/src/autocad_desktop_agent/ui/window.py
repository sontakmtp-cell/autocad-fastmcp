"""Vietnamese Phase 4 C1 lab window and system tray."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from ..state import AgentIntent, AgentViewState, STATE_COPY


class CoreFacade(Protocol):
    @property
    def view_state(self) -> AgentViewState: ...
    def subscribe(self, callback: Any) -> None: ...
    def handle_intent(self, intent: AgentIntent, diagnostics_target: Path | None = None) -> None: ...
    async def run_forever(self) -> None: ...


class StateBridge(QObject):
    changed = Signal(object)


class AgentRunner(threading.Thread):
    def __init__(self, core: CoreFacade) -> None:
        super().__init__(name="AutoCADAgentCore", daemon=True)
        self.core = core

    def run(self) -> None:
        asyncio.run(self.core.run_forever())


class AgentWindow(QMainWindow):
    def __init__(self, core: CoreFacade, diagnostics_dir: Path) -> None:
        super().__init__()
        self.core = core
        self.diagnostics_dir = diagnostics_dir
        self.bridge = StateBridge()
        self.bridge.changed.connect(self.render)
        self._last_state = core.view_state
        self.setWindowTitle("Kỹ Thuật Vàng AutoCAD Agent")
        self.setFont(QFont("Segoe UI", 10))
        self.setMinimumSize(520, 360)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        title = QLabel("Kỹ Thuật Vàng AutoCAD Agent")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        self.primary = QLabel()
        self.primary.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        layout.addWidget(self.primary)
        layout.addWidget(self.detail)

        grid = QGridLayout()
        self.values: dict[str, QLabel] = {}
        for row, (key, label) in enumerate(
            [
                ("device", "Thiết bị"),
                ("server", "Máy chủ"),
                ("autocad", "AutoCAD"),
                ("runtime", "Runtime"),
                ("component", "Thành phần AutoCAD"),
                ("capability", "Khả năng"),
                ("document", "Bản vẽ"),
                ("task", "Tác vụ"),
                ("version", "Phiên bản"),
                ("support", "Mã hỗ trợ"),
            ]
        ):
            grid.addWidget(QLabel(label), row, 0)
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(value, row, 1)
            self.values[key] = value
        layout.addLayout(grid)

        actions = QHBoxLayout()
        self.retry_button = QPushButton("Thử lại")
        self.pause_button = QPushButton("Tạm dừng")
        self.diagnostics_button = QPushButton("Chẩn đoán")
        self.help_button = QPushButton("Trợ giúp")
        for button in (
            self.retry_button,
            self.pause_button,
            self.diagnostics_button,
            self.help_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.setCentralWidget(root)

        self.retry_button.clicked.connect(lambda: core.handle_intent(AgentIntent.RETRY))
        self.pause_button.clicked.connect(self._toggle_pause)
        self.diagnostics_button.clicked.connect(self._diagnostics)
        self.help_button.clicked.connect(self._help)

        agent_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(agent_icon)
        self.tray = QSystemTrayIcon(agent_icon, self)
        tray_menu = QMenu()
        self.tray_status = QAction("Agent", self)
        self.tray_status.setEnabled(False)
        tray_menu.addAction(self.tray_status)
        tray_menu.addSeparator()
        open_action = tray_menu.addAction("Mở Agent")
        self.tray_pause = tray_menu.addAction("Tạm dừng mọi tác vụ")
        diagnostics_action = tray_menu.addAction("Chẩn đoán")
        exit_action = tray_menu.addAction("Thoát Agent")
        open_action.triggered.connect(self._show_from_tray)
        self.tray_pause.triggered.connect(self._toggle_pause)
        diagnostics_action.triggered.connect(self._diagnostics)
        exit_action.triggered.connect(self._exit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(lambda *_: self._show_from_tray())
        self.tray.show()
        self.core.subscribe(self.bridge.changed.emit)
        self.render(core.view_state)

    def render(self, state: AgentViewState) -> None:
        self._last_state = state
        title, detail = STATE_COPY[state.runtime_state]
        self.primary.setText(title)
        self.detail.setText(detail)
        self.values["device"].setText(state.device_name)
        self.values["server"].setText("Đã kết nối" if state.server_connected else "Chưa kết nối")
        self.values["autocad"].setText(self._product_text(state))
        self.values["runtime"].setText(state.runtime_label)
        self.values["component"].setText(self._component_text(state))
        self.values["capability"].setText(self._capability_text(state))
        self.values["document"].setText(state.document_name or "Chưa có")
        self.values["task"].setText(state.current_task or "Không có")
        self.values["version"].setText(self._version_text(state))
        self.values["support"].setText(state.support_code or "—")
        label = "Tiếp tục" if state.paused else "Tạm dừng"
        self.pause_button.setText(label)
        self.tray_pause.setText("Tiếp tục mọi tác vụ" if state.paused else "Tạm dừng mọi tác vụ")
        self.tray_status.setText(f"Máy chủ: {'Đã kết nối' if state.server_connected else 'Mất kết nối'}")
        self.tray.setToolTip(
            f"AutoCAD Agent · {state.runtime_label}"
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "AutoCAD Agent vẫn đang chạy",
            "Mở lại Agent từ biểu tượng ở khay hệ thống.",
        )

    def _toggle_pause(self) -> None:
        intent = AgentIntent.RESUME if self._last_state.paused else AgentIntent.PAUSE
        self.core.handle_intent(intent)

    def _diagnostics(self) -> None:
        target = self.diagnostics_dir / "autocad-agent-diagnostics.json"
        self.core.handle_intent(AgentIntent.EXPORT_DIAGNOSTICS, target)
        QMessageBox.information(
            self,
            "Chẩn đoán",
            "Đã tạo gói chẩn đoán đã loại thông tin nhạy cảm.",
        )

    def _help(self) -> None:
        QMessageBox.information(
            self,
            "Trợ giúp",
            "Hãy mở AutoCAD và một bản vẽ. Nếu vẫn chưa sẵn sàng, bấm Thử lại rồi tạo tệp Chẩn đoán.",
        )

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    @staticmethod
    def _product_text(state: AgentViewState) -> str:
        if not state.product:
            return state.autocad_state
        release = f" {state.release_year}" if state.release_year else ""
        return f"{state.product}{release} · {state.autocad_state}"

    @staticmethod
    def _component_text(state: AgentViewState) -> str:
        if state.edition == "lt":
            return (
                f"AutoLISP package {state.package_version} · Hoạt động"
                if state.runtime_id == "autolisp_file_ipc"
                else "AutoLISP package · Chưa sẵn sàng"
            )
        if state.runtime_id == "managed_dotnet":
            if state.host_handshake_state == "connected":
                suffix = (
                    f" {state.host_family} {state.host_version}"
                    if state.host_family and state.host_version
                    else ""
                )
                return f"Managed Host{suffix} · Hoạt động"
            return "Managed Host · Cần cài hoặc tải lại"
        if state.runtime_role == "compatibility_fallback":
            return f"AutoLISP package {state.package_version} · Hoạt động"
        return "Chưa kiểm tra"

    @staticmethod
    def _capability_text(state: AgentViewState) -> str:
        if state.edition == "lt":
            return "Portable core"
        if state.degradation_reason or state.runtime_role == "compatibility_fallback":
            return "Chỉ đọc giới hạn"
        if state.runtime_id == "managed_dotnet":
            return "Đọc bản vẽ qua Managed .NET"
        return "Chưa xác định"

    @staticmethod
    def _version_text(state: AgentViewState) -> str:
        host = (
            f" · Host {state.host_family} {state.host_version}"
            if state.host_family and state.host_version
            else ""
        )
        return f"Agent {state.agent_version} · AutoLISP {state.package_version}{host}"

    def _exit(self) -> None:
        if self.core.view_state.current_task:
            answer = QMessageBox.question(
                self,
                "Tác vụ đang chạy",
                "Agent đang thực hiện tác vụ. Thoát có thể cần kiểm tra lại kết quả. Vẫn thoát?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.core.handle_intent(AgentIntent.EXIT)
        self.tray.hide()
        QApplication.quit()


def run_ui(core: CoreFacade, diagnostics_dir: Path) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = AgentWindow(core, diagnostics_dir)
    runner = AgentRunner(core)
    runner.start()
    window.show()
    return app.exec()
