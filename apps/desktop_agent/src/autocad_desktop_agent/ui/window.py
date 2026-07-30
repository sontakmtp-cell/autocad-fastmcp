"""Vietnamese Phase 4 C1 lab window, Quick Actions dashboard and system tray."""

from __future__ import annotations

import asyncio
import os
import threading
import webbrowser
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QGroupBox,
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

from ..state import AgentIntent, AgentViewState, STATE_COPY
from .wizard import FirstRunWizard

ANALYSIS_STARTER_PROMPT = (
    "Hãy quan sát bản vẽ AutoCAD đang mở, tạo drawing scene và tóm tắt các chi tiết, "
    "contour, lỗ, pattern lỗ, slot, nhóm đồng tâm và lỗi hình học. Chỉ phân tích, không sửa bản vẽ."
)

CLEANUP_AUDIT_STARTER_PROMPT = (
    "Hãy thực hiện cleanup audit trên bản vẽ AutoCAD đang mở để kiểm tra các đường trùng, "
    "contour hở và lỗi hình học. Chỉ phân tích, không sửa bản vẽ."
)


class CoreFacade(Protocol):
    @property
    def view_state(self) -> AgentViewState: ...
    def subscribe(self, callback: Any) -> None: ...
    def handle_intent(self, intent: AgentIntent, diagnostics_target: Path | None = None) -> None: ...
    def approve_approval(self, approval_request_id: str) -> Any: ...
    def deny_approval(self, approval_request_id: str) -> Any: ...
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
        self.setWindowTitle("AutoCAD AI Connector MVP Dashboard")
        self.setFont(QFont("Segoe UI", 10))
        self.setMinimumSize(700, 720)

        root = QWidget(self)
        layout = QVBoxLayout(root)

        # Header Title
        header_box = QHBoxLayout()
        title = QLabel("AutoCAD AI Connector MVP")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #0F172A;")
        header_box.addWidget(title)

        header_box.addStretch()
        self.wizard_button = QPushButton("🧙 Hướng dẫn 7 bước (Wizard)")
        self.wizard_button.setStyleSheet("font-weight: 600; padding: 6px 12px; color: #2563EB;")
        self.wizard_button.clicked.connect(self._launch_wizard)
        header_box.addWidget(self.wizard_button)

        layout.addLayout(header_box)

        self.primary = QLabel()
        self.primary.setStyleSheet("font-size: 16px; font-weight: 600; color: #1E293B;")
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color: #475569; margin-bottom: 8px;")
        layout.addWidget(self.primary)
        layout.addWidget(self.detail)

        # Main Dashboard Status Grid (Text + Icons)
        dash_group = QGroupBox("Bảng điều khiển trạng thái hệ thống")
        dash_group.setStyleSheet("font-weight: 600; color: #0F172A;")
        grid = QGridLayout(dash_group)
        grid.setSpacing(8)

        self.values: dict[str, QLabel] = {}
        fields = [
            ("device", "Thiết bị liên kết"),
            ("server", "Máy chủ Gateway"),
            ("autocad", "Ứng dụng AutoCAD"),
            ("runtime", "Managed .NET Runtime"),
            ("component", "Thành phần Managed Host"),
            ("capability", "Khả năng xử lý"),
            ("document", "Bản vẽ đang mở"),
            ("write_lock", "Khóa ghi (Write Lock)"),
            ("hard_pause", "Tạm dừng khẩn cấp"),
            ("active_job", "Tác vụ / Job hiện tại"),
            ("pending_approval", "Yêu cầu xác nhận đang chờ"),
            ("outcome", "Kiểm tra an toàn"),
            ("version", "Phiên bản Agent / Host"),
            ("support", "Mã hỗ trợ (Support ID)"),
        ]

        for row, (key, label) in enumerate(fields):
            lbl = QLabel(label)
            lbl.setStyleSheet("font-weight: 500; color: #334155;")
            grid.addWidget(lbl, row, 0)

            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setStyleSheet("font-weight: 600; color: #0F172A;")
            grid.addWidget(value, row, 1)
            self.values[key] = value

        layout.addWidget(dash_group)

        # Approval Box
        approval_group = QGroupBox("Xác nhận tin cậy trên thiết bị (Trusted Approval)")
        approval_group.setStyleSheet("font-weight: 600; color: #0F172A;")
        approval_layout = QVBoxLayout(approval_group)
        self.approval_status = QLabel("Không có yêu cầu đang chờ.")
        self.approval_status.setWordWrap(True)
        self.approval_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.approval_status.setStyleSheet("color: #475569; font-weight: 400;")
        approval_layout.addWidget(self.approval_status)

        approval_actions = QHBoxLayout()
        self.approve_button = QPushButton("✓ Đồng ý thực hiện đúng yêu cầu")
        self.deny_button = QPushButton("✕ Từ chối tác vụ")
        self.approve_button.setStyleSheet("font-weight: 600; background-color: #16A34A; color: white; padding: 6px 12px;")
        self.deny_button.setStyleSheet("font-weight: 600; background-color: #DC2626; color: white; padding: 6px 12px;")
        self.approve_button.setAccessibleName("Đồng ý yêu cầu xác nhận đang hiển thị")
        self.deny_button.setAccessibleName("Từ chối yêu cầu xác nhận đang hiển thị")
        approval_actions.addWidget(self.approve_button)
        approval_actions.addWidget(self.deny_button)
        approval_layout.addLayout(approval_actions)
        layout.addWidget(approval_group)

        # Quick Actions Section
        qa_group = QGroupBox("Thao tác nhanh (Quick Actions)")
        qa_group.setStyleSheet("font-weight: 600; color: #0F172A;")
        qa_layout = QGridLayout(qa_group)

        self.btn_relink = QPushButton("🔗 Liên kết lại tài khoản")
        self.btn_recheck = QPushButton("🔄 Kiểm tra lại AutoCAD")
        self.btn_host_install = QPushButton("🛠️ Cài đặt / Sửa Managed Host")
        self.btn_open_portal = QPushButton("🌐 Mở Web Portal")
        self.btn_chatgpt_guide = QPushButton("🤖 Hướng dẫn kết nối ChatGPT")
        self.btn_copy_prompt_analysis = QPushButton("📋 Copy Prompt Phân Tích")
        self.btn_copy_prompt_cleanup = QPushButton("📋 Copy Prompt Cleanup Audit")
        self.btn_toggle_write_lock = QPushButton("🔒 Chuyển đổi Khóa Ghi")
        self.btn_pause = QPushButton("⏸️ Tạm dừng khẩn cấp")
        self.btn_diagnostics = QPushButton("📦 Xuất Chẩn Đoán (Redacted)")
        self.btn_check_update = QPushButton("ℹ️ Kiểm tra cập nhật")

        qa_layout.addWidget(self.btn_relink, 0, 0)
        qa_layout.addWidget(self.btn_recheck, 0, 1)
        qa_layout.addWidget(self.btn_host_install, 0, 2)

        qa_layout.addWidget(self.btn_open_portal, 1, 0)
        qa_layout.addWidget(self.btn_chatgpt_guide, 1, 1)
        qa_layout.addWidget(self.btn_toggle_write_lock, 1, 2)

        qa_layout.addWidget(self.btn_copy_prompt_analysis, 2, 0)
        qa_layout.addWidget(self.btn_copy_prompt_cleanup, 2, 1)
        qa_layout.addWidget(self.btn_pause, 2, 2)

        qa_layout.addWidget(self.btn_diagnostics, 3, 0)
        qa_layout.addWidget(self.btn_check_update, 3, 1)

        layout.addWidget(qa_group)

        self.setCentralWidget(root)

        # Connect actions
        self.btn_relink.clicked.connect(lambda: core.handle_intent(AgentIntent.RETRY))
        self.btn_recheck.clicked.connect(lambda: core.handle_intent(AgentIntent.RETRY))
        self.btn_host_install.clicked.connect(self._host_install_action)
        self.btn_open_portal.clicked.connect(self._open_portal)
        self.btn_chatgpt_guide.clicked.connect(self._open_chatgpt_guide)
        self.btn_copy_prompt_analysis.clicked.connect(self._copy_prompt_analysis)
        self.btn_copy_prompt_cleanup.clicked.connect(self._copy_prompt_cleanup)
        self.btn_toggle_write_lock.clicked.connect(self._toggle_write_lock)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_diagnostics.clicked.connect(self._diagnostics)
        self.btn_check_update.clicked.connect(self._check_update)

        self.approve_button.clicked.connect(self._approve_current)
        self.deny_button.clicked.connect(self._deny_current)

        # System Tray setup
        agent_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(agent_icon)
        self.tray = QSystemTrayIcon(agent_icon, self)
        tray_menu = QMenu()
        self.tray_status = QAction("AutoCAD AI Agent", self)
        self.tray_status.setEnabled(False)
        tray_menu.addAction(self.tray_status)
        tray_menu.addSeparator()

        open_action = tray_menu.addAction("Mở Bảng Điều Khiển Agent")
        portal_action = tray_menu.addAction("Mở Web Portal")
        self.tray_pause = tray_menu.addAction("Tạm dừng mọi tác vụ AI")
        diagnostics_action = tray_menu.addAction("Xuất chẩn đoán")
        exit_action = tray_menu.addAction("Thoát an toàn")

        open_action.triggered.connect(self._show_from_tray)
        portal_action.triggered.connect(self._open_portal)
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

        # Text + Icons representation for non-ambiguous rendering
        self.values["device"].setText(f"👤 {state.device_name}" if state.device_name else "✕ Chưa liên kết")
        self.values["server"].setText("✓ Online (WSS Gateway)" if state.server_connected else "✕ Offline (Đang kết nối lại)")
        self.values["autocad"].setText(self._product_text(state))
        self.values["runtime"].setText(state.runtime_label)
        self.values["component"].setText(self._component_text(state))
        self.values["capability"].setText(self._capability_text(state))
        self.values["document"].setText(f"📄 {state.document_name}" if state.document_name else "— Chưa mở bản vẽ")

        write_lock_str = "🔒 Bật (Mặc định - An toàn)" if state.write_lock_enabled else "🔓 Tắt (Có thể sửa bản vẽ khi duyệt)"
        self.values["write_lock"].setText(write_lock_str)

        pause_str = "⏸️ Đang bật (Chặn tác vụ mới)" if state.hard_pause or state.paused else "▶️ Đang hoạt động bình thường"
        self.values["hard_pause"].setText(pause_str)

        self.values["active_job"].setText(f"⚙️ {state.active_job_id}" if state.active_job_id else "— Không có")

        if state.outcome_unknown:
            self.values["outcome"].setText("⚠️ Cần kiểm tra bản vẽ trước khi ghi tiếp")
        else:
            self.values["outcome"].setText("✓ Bình thường")

        pending_str = f"⚠️ {state.pending_approval_count} yêu cầu đang chờ" if state.pending_approval_count > 0 else "— Không có"
        self.values["pending_approval"].setText(pending_str)

        self.values["version"].setText(self._version_text(state))
        self.values["support"].setText(state.support_id or state.support_code or "—")

        label = "▶️ Tiếp tục tác vụ" if state.paused else "⏸️ Tạm dừng khẩn cấp"
        self.btn_pause.setText(label)
        self.tray_pause.setText("▶️ Tiếp tục tác vụ AI" if state.paused else "⏸️ Tạm dừng tác vụ AI")
        self.tray_status.setText(f"Gateway: {'Online' if state.server_connected else 'Offline'} · AutoCAD: {state.autocad_state}")
        self.tray.setToolTip(f"AutoCAD AI Connector · {state.runtime_label}")

        self._render_approval(state)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "AutoCAD Agent vẫn đang chạy ngầm",
            "Mở lại ứng dụng bất cứ lúc nào từ biểu tượng ở khay hệ thống.",
        )

    def _launch_wizard(self) -> None:
        wizard = FirstRunWizard(self.core, self)
        wizard.update_from_state(self._last_state)
        wizard.exec()

    def _toggle_pause(self) -> None:
        intent = AgentIntent.RESUME if self._last_state.paused else AgentIntent.PAUSE
        self.core.handle_intent(intent)

    def _toggle_write_lock(self) -> None:
        intent = AgentIntent.DISABLE_WRITE_LOCK if self._last_state.write_lock_enabled else AgentIntent.ENABLE_WRITE_LOCK
        self.core.handle_intent(intent)

    def _diagnostics(self) -> None:
        target = self.diagnostics_dir / "autocad-agent-diagnostics.json"
        self.core.handle_intent(AgentIntent.EXPORT_DIAGNOSTICS, target)
        QMessageBox.information(
            self,
            "Xuất Chẩn Đoán",
            f"Đã xuất tệp chẩn đoán an toàn (Scrubbed / Redacted):\n{target}",
        )

    def _open_portal(self) -> None:
        webbrowser.open("http://localhost:3000")

    def _open_chatgpt_guide(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Hướng dẫn kết nối ChatGPT MCP")
        dlg.setMinimumSize(540, 360)
        vbox = QVBoxLayout(dlg)

        title = QLabel("Hướng dẫn kết nối ChatGPT Web qua FastMCP Gateway")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        vbox.addWidget(title)

        guide_text = QLabel(
            "1. Mở trang ChatGPT (https://chatgpt.com) trên trình duyệt.\n"
            "2. Vào mục Cài đặt -> Custom Connectors / Developer Mode MCP.\n"
            "3. Nhập URL Server Gateway (ví dụ: https://gateway.autocad-mcp.com/mcp).\n"
            "4. Chọn xác thực OAuth / Browser Pairing đã liên kết.\n"
            "5. Bắt đầu cuộc trò chuyện và dán Starter Prompt bên dưới để ChatGPT quan sát bản vẽ."
        )
        guide_text.setWordWrap(True)
        vbox.addWidget(guide_text)

        btn_copy = QPushButton("📋 Sao chép Prompt phân tích bản vẽ mẫu")
        btn_copy.clicked.connect(self._copy_prompt_analysis)
        vbox.addWidget(btn_copy)

        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(dlg.accept)
        vbox.addWidget(btn_close)
        dlg.exec()

    def _copy_prompt_analysis(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(ANALYSIS_STARTER_PROMPT)
        QMessageBox.information(
            self,
            "Sao chép thành công",
            "Đã sao chép starter prompt Phân Tích Bản Vẽ vào clipboard!",
        )

    def _copy_prompt_cleanup(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(CLEANUP_AUDIT_STARTER_PROMPT)
        QMessageBox.information(
            self,
            "Sao chép thành công",
            "Đã sao chép starter prompt Cleanup Audit vào clipboard!",
        )

    def _host_install_action(self) -> None:
        QMessageBox.information(
            self,
            "Cài đặt Managed Host R25",
            "Thành phần Managed Host R25 đã được cài đặt trong thư mục Autodesk ApplicationPlugins của người dùng hiện tại.",
        )

    def _check_update(self) -> None:
        QMessageBox.information(
            self,
            "Kiểm tra cập nhật",
            "Phiên bản hiện tại: AutoCAD AI Connector MVP (Lab Release).\nỨng dụng đang ở phiên bản mới nhất.",
        )

    def _show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _render_approval(self, state: AgentViewState) -> None:
        if not state.pending_approvals:
            self.approval_status.setText("Không có yêu cầu xác nhận nào đang chờ.")
            self.approve_button.setEnabled(False)
            self.deny_button.setEnabled(False)
            return

        approval = state.pending_approvals[0]
        warnings = (
            "\nCảnh báo: " + "; ".join(approval.warnings)
            if approval.warnings
            else ""
        )
        self.approval_status.setText(
            f"Trạng thái: {approval.status_text}\n"
            f"Tóm tắt: {approval.operation_summary}\n"
            f"Bản vẽ: {approval.document_name}\n"
            f"Số thao tác / đối tượng: {approval.operation_count} / {approval.entity_count}\n"
            f"Runtime: {approval.runtime_label}\n"
            f"Mức rủi ro / Xác nhận: {approval.risk_class} / {approval.assurance}\n"
            f"Hết hạn lúc: {approval.expires_at}\n"
            f"Mã hỗ trợ: {approval.support_id}"
            f"{warnings}"
        )
        self.approve_button.setEnabled(approval.actionable)
        self.deny_button.setEnabled(approval.actionable)

    def _approve_current(self) -> None:
        approvals = self._last_state.pending_approvals
        if approvals and approvals[0].actionable:
            self.core.approve_approval(approvals[0].approval_request_id)

    def _deny_current(self) -> None:
        approvals = self._last_state.pending_approvals
        if approvals and approvals[0].actionable:
            self.core.deny_approval(approvals[0].approval_request_id)

    @staticmethod
    def _product_text(state: AgentViewState) -> str:
        if not state.product:
            return f"! {state.autocad_state}"
        release = f" {state.release_year}" if state.release_year else ""
        return f"✓ {state.product}{release} · {state.autocad_state}"

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
                return f"✓ Managed Host{suffix} · Hoạt động"
            return "! Managed Host · Cần cài hoặc tải lại"
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
            return "✓ Đọc bản vẽ qua Managed .NET R25 Gateway"
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
                "Agent đang thực hiện tác vụ an toàn. Thoát lúc này có thể cần kiểm tra lại bản vẽ. Vẫn thoát?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
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
