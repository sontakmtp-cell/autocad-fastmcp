"""Vietnamese Phase 4 C1 lab window and system tray with a Dark Slate design."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..state import AgentIntent, AgentViewState, RuntimeState, STATE_COPY

DARK_SLATE_STYLESHEET = """
QMainWindow {
    background-color: #0B0F19;
    color: #F1F5F9;
}

QWidget {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    color: #F1F5F9;
}

QFrame#headerCard, QFrame#statusCard, QFrame#controlCard, QFrame#footerCard {
    background-color: #151D2A;
    border: 1px solid #232F42;
    border-radius: 10px;
}

QFrame#statusBanner {
    background-color: #1A2638;
    border: 1px solid #2B3B54;
    border-radius: 8px;
    padding: 12px;
}

QLabel#appTitle {
    font-size: 17px;
    font-weight: 700;
    color: #38BDF8;
}

QLabel#deviceTag {
    background-color: #1E293B;
    border: 1px solid #334155;
    color: #94A3B8;
    font-size: 11px;
    font-weight: 600;
    border-radius: 12px;
    padding: 3px 10px;
}

QLabel#primaryStatus {
    font-size: 16px;
    font-weight: 700;
    color: #F8FAFC;
}

QLabel#detailStatus {
    font-size: 12px;
    color: #94A3B8;
    line-height: 1.4;
}

QLabel#sectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: #38BDF8;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QLabel#fieldLabel {
    font-size: 12px;
    color: #94A3B8;
    font-weight: 500;
}

QLabel#fieldValue {
    font-size: 12px;
    color: #F1F5F9;
    font-weight: 600;
}

QPushButton {
    background-color: #1E293B;
    color: #F1F5F9;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #2D3D54;
    border-color: #475569;
}

QPushButton:pressed {
    background-color: #0F172A;
}

QPushButton#retryButton {
    background-color: #0284C7;
    color: #FFFFFF;
    border: 1px solid #38BDF8;
}

QPushButton#retryButton:hover {
    background-color: #0369A1;
}

QPushButton#pauseButton {
    background-color: #334155;
    color: #F8FAFC;
    border: 1px solid #475569;
}

QPushButton#pauseButton:hover {
    background-color: #475569;
}

QPushButton#pauseButton[paused="true"] {
    background-color: #78350F;
    color: #FDE68A;
    border: 1px solid #F59E0B;
}

QMenu {
    background-color: #151D2A;
    border: 1px solid #232F42;
    color: #F1F5F9;
    padding: 4px;
    border-radius: 6px;
}

QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
    font-size: 12px;
}

QMenu::item:selected {
    background-color: #0284C7;
    color: #FFFFFF;
}

QMenu::separator {
    height: 1px;
    background-color: #232F42;
    margin: 4px 0px;
}
"""


class CoreFacade(Protocol):
    @property
    def view_state(self) -> AgentViewState: ...

    def subscribe(self, callback: Any) -> None: ...

    def handle_intent(
        self,
        intent: AgentIntent,
        diagnostics_target: Path | None = None,
    ) -> None: ...

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
        self.setMinimumSize(540, 480)
        self.setStyleSheet(DARK_SLATE_STYLESHEET)

        root = QWidget(self)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        main_layout.addWidget(self._build_header_card())
        main_layout.addWidget(self._build_status_banner())
        main_layout.addWidget(self._build_status_card())
        main_layout.addWidget(self._build_safety_card())
        main_layout.addLayout(self._build_action_buttons())
        self.setCentralWidget(root)

        self.retry_button.clicked.connect(
            lambda: core.handle_intent(AgentIntent.RETRY)
        )
        self.pause_button.clicked.connect(self._toggle_pause)
        self.diagnostics_button.clicked.connect(self._diagnostics)
        self.help_button.clicked.connect(self._help)

        self._setup_system_tray()
        self.core.subscribe(self.bridge.changed.emit)
        self.render(core.view_state)

    def _build_header_card(self) -> QFrame:
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)

        header_title_box = QVBoxLayout()
        title_label = QLabel("Kỹ Thuật Vàng AutoCAD Agent")
        title_label.setObjectName("appTitle")
        subtitle_label = QLabel("Desktop Gateway Agent · Windows")
        subtitle_label.setStyleSheet("color: #64748B; font-size: 11px;")
        header_title_box.addWidget(title_label)
        header_title_box.addWidget(subtitle_label)

        self.device_badge = QLabel("Thiết bị: —")
        self.device_badge.setObjectName("deviceTag")

        header_layout.addLayout(header_title_box)
        header_layout.addStretch()
        header_layout.addWidget(self.device_badge)
        return header_card

    def _build_status_banner(self) -> QFrame:
        self.status_banner = QFrame()
        self.status_banner.setObjectName("statusBanner")
        banner_layout = QVBoxLayout(self.status_banner)
        banner_layout.setContentsMargins(14, 12, 14, 12)
        banner_layout.setSpacing(4)

        self.primary = QLabel()
        self.primary.setObjectName("primaryStatus")
        self.detail = QLabel()
        self.detail.setObjectName("detailStatus")
        self.detail.setWordWrap(True)
        banner_layout.addWidget(self.primary)
        banner_layout.addWidget(self.detail)
        return self.status_banner

    def _build_status_card(self) -> QFrame:
        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(8)

        status_header = QLabel("Trạng thái kết nối & Hệ thống")
        status_header.setObjectName("sectionTitle")
        status_layout.addWidget(status_header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.values: dict[str, QLabel] = {}
        items = [
            ("device", "Thiết bị local"),
            ("server", "Máy chủ Gateway"),
            ("autocad", "AutoCAD"),
            ("document", "Bản vẽ active"),
            ("task", "Tác vụ từ xa"),
            ("version", "Phiên bản Agent"),
            ("support", "Mã hỗ trợ"),
        ]
        for row, (key, label_text) in enumerate(items):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            value = QLabel("—")
            value.setObjectName("fieldValue")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self.values[key] = value

        status_layout.addLayout(grid)
        return status_card

    def _build_safety_card(self) -> QFrame:
        control_card = QFrame()
        control_card.setObjectName("controlCard")
        control_layout = QVBoxLayout(control_card)
        control_layout.setContentsMargins(14, 12, 14, 12)
        control_layout.setSpacing(8)

        control_header = QLabel("Điều khiển an toàn local")
        control_header.setObjectName("sectionTitle")
        control_layout.addWidget(control_header)

        control_grid = QGridLayout()
        control_grid.setHorizontalSpacing(16)
        control_grid.setVerticalSpacing(6)

        write_label = QLabel("Quyền chỉnh sửa từ xa")
        write_label.setObjectName("fieldLabel")
        self.wlock_val = QLabel("TẮT (Agent C1 chỉ đọc)")

        risk_label = QLabel("Chế độ thực thi")
        risk_label.setObjectName("fieldLabel")
        self.rmode_val = QLabel("Chỉ đọc (không Preview/Commit)")

        control_grid.addWidget(write_label, 0, 0)
        control_grid.addWidget(self.wlock_val, 0, 1)
        control_grid.addWidget(risk_label, 1, 0)
        control_grid.addWidget(self.rmode_val, 1, 1)
        control_layout.addLayout(control_grid)
        return control_card

    def _build_action_buttons(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.retry_button = QPushButton("Thử lại")
        self.retry_button.setObjectName("retryButton")
        self.pause_button = QPushButton("Tạm dừng")
        self.pause_button.setObjectName("pauseButton")
        self.diagnostics_button = QPushButton("Chẩn đoán")
        self.help_button = QPushButton("Trợ giúp")
        for button in (
            self.retry_button,
            self.pause_button,
            self.diagnostics_button,
            self.help_button,
        ):
            actions.addWidget(button)
        return actions

    def _setup_system_tray(self) -> None:
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

    def render(self, state: AgentViewState) -> None:
        self._last_state = state
        title, detail = STATE_COPY[state.runtime_state]
        self.primary.setText(title)
        self.detail.setText(detail)
        self._render_status_banner(state.runtime_state)

        self.device_badge.setText(f"Thiết bị: {state.device_name}")
        self.values["device"].setText(state.device_name)
        self.values["server"].setText(
            "● Đã kết nối" if state.server_connected else "○ Mất kết nối"
        )
        self.values["server"].setStyleSheet(
            "color: #10B981;" if state.server_connected else "color: #EF4444;"
        )
        self.values["autocad"].setText(state.autocad_state)
        self.values["document"].setText(state.document_name or "Chưa có")
        self.values["task"].setText(state.current_task or "Không có")
        self.values["version"].setText(
            f"Agent {state.agent_version} · Package {state.package_version}"
        )
        self.values["support"].setText(state.support_code or "—")

        self._render_safety_state(state)

        label = "Tiếp tục" if state.paused else "Tạm dừng"
        self.pause_button.setText(label)
        self.pause_button.setProperty("paused", "true" if state.paused else "false")
        self.pause_button.style().unpolish(self.pause_button)
        self.pause_button.style().polish(self.pause_button)

        self.tray_pause.setText(
            "Tiếp tục mọi tác vụ" if state.paused else "Tạm dừng mọi tác vụ"
        )
        self.tray_status.setText(
            f"Máy chủ: {'Đã kết nối' if state.server_connected else 'Mất kết nối'}"
        )

    def _render_status_banner(self, runtime_state: RuntimeState) -> None:
        if runtime_state == RuntimeState.READY:
            banner_style = (
                "background-color: #064E3B; border: 1px solid #059669; "
                "border-radius: 8px; padding: 12px;"
            )
            primary_style = "color: #A7F3D0; font-size: 16px; font-weight: 700;"
        elif runtime_state in {
            RuntimeState.CONNECTING,
            RuntimeState.BUSY_REMOTE,
            RuntimeState.BUSY_USER,
        }:
            banner_style = (
                "background-color: #78350F; border: 1px solid #D97706; "
                "border-radius: 8px; padding: 12px;"
            )
            primary_style = "color: #FDE68A; font-size: 16px; font-weight: 700;"
        elif runtime_state == RuntimeState.PAUSED:
            banner_style = (
                "background-color: #581C87; border: 1px solid #9333EA; "
                "border-radius: 8px; padding: 12px;"
            )
            primary_style = "color: #E9D5FF; font-size: 16px; font-weight: 700;"
        else:
            banner_style = (
                "background-color: #7F1D1D; border: 1px solid #DC2626; "
                "border-radius: 8px; padding: 12px;"
            )
            primary_style = "color: #FCA5A5; font-size: 16px; font-weight: 700;"
        self.status_banner.setStyleSheet(banner_style)
        self.primary.setStyleSheet(primary_style)

    def _render_safety_state(self, state: AgentViewState) -> None:
        if state.paused:
            self.wlock_val.setText("TẮT (Đã tạm dừng)")
            self.wlock_val.setStyleSheet(
                "color: #F59E0B; font-weight: 600; font-size: 12px;"
            )
            self.rmode_val.setText("Tạm dừng (chặn tác vụ mới)")
            self.rmode_val.setStyleSheet(
                "color: #E9D5FF; font-weight: 600; font-size: 12px;"
            )
            return

        self.wlock_val.setText("TẮT (Agent C1 chỉ đọc)")
        self.wlock_val.setStyleSheet(
            "color: #F87171; font-weight: 600; font-size: 12px;"
        )
        self.rmode_val.setText("Chỉ đọc (không Preview/Commit)")
        self.rmode_val.setStyleSheet(
            "color: #38BDF8; font-weight: 600; font-size: 12px;"
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
            f"Đã tạo tệp chẩn đoán:\n{target}",
        )

    def _help(self) -> None:
        QMessageBox.information(
            self,
            "Trợ giúp Kỹ Thuật Vàng Agent",
            "Hãy mở AutoCAD và một bản vẽ DWG.\n\n"
            "• Nếu trạng thái chưa sẵn sàng, bấm 'Thử lại'.\n"
            "• Nếu cần kiểm tra kỹ thuật, bấm 'Chẩn đoán' để xuất file log.",
        )

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

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
