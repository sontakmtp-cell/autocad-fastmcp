"""Vietnamese First-run Onboarding Wizard for AutoCAD AI Connector MVP."""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..state import AgentViewState


class CoreFacade(Protocol):
    @property
    def view_state(self) -> AgentViewState: ...
    def handle_intent(self, intent: Any, diagnostics_target: Any = None) -> None: ...


class FirstRunWizard(QDialog):
    """7-step onboarding wizard for non-technical AutoCAD users."""

    finished_wizard = Signal()

    def __init__(self, core: CoreFacade, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.core = core
        self.setWindowTitle("Hướng dẫn thiết lập AutoCAD AI Connector MVP")
        self.setMinimumSize(640, 520)
        self.setFont(QFont("Segoe UI", 10))

        layout = QVBoxLayout(self)

        # Header
        header = QLabel("Cấu hình & Kết nối sản phẩm AutoCAD AI Connector")
        header.setStyleSheet("font-size: 18px; font-weight: 700; color: #1E293B; margin-bottom: 8px;")
        layout.addWidget(header)

        # Progress Indicator Bar
        self.step_label = QLabel("Bước 1 / 7 — Chào mừng")
        self.step_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #2563EB;")
        layout.addWidget(self.step_label)

        # Content pages
        self.stack = QStackedWidget(self)
        self.pages: list[QWidget] = []
        for i in range(7):
            page = self._create_page(i)
            self.pages.append(page)
            self.stack.addWidget(page)
        layout.addWidget(self.stack, stretch=1)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # Bottom Controls
        bottom_layout = QHBoxLayout()
        self.support_id_label = QLabel("Mã hỗ trợ: —")
        self.support_id_label.setStyleSheet("color: #64748B; font-size: 11px;")
        bottom_layout.addWidget(self.support_id_label)

        bottom_layout.addStretch()

        self.prev_btn = QPushButton("Quay lại")
        self.next_btn = QPushButton("Tiếp theo")
        self.retry_btn = QPushButton("Thử lại bước này")
        self.finish_btn = QPushButton("Hoàn tất")

        self.prev_btn.clicked.connect(self._on_prev)
        self.next_btn.clicked.connect(self._on_next)
        self.retry_btn.clicked.connect(self._on_retry)
        self.finish_btn.clicked.connect(self._on_finish)

        bottom_layout.addWidget(self.prev_btn)
        bottom_layout.addWidget(self.retry_btn)
        bottom_layout.addWidget(self.next_btn)
        bottom_layout.addWidget(self.finish_btn)

        layout.addLayout(bottom_layout)

        self._update_step_ui()

    def _create_page(self, step_index: int) -> QWidget:
        page = QWidget()
        vbox = QVBoxLayout(page)
        vbox.setContentsMargins(12, 12, 12, 12)

        if step_index == 0:
            title = QLabel("1. Chào mừng tới AutoCAD AI Connector MVP")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            desc = QLabel(
                "Ứng dụng cho phép kết nối bản vẽ AutoCAD Mechanical 2025/2026 với ChatGPT "
                "để phân tích hình học, kiểm tra lỗi bản vẽ và hỗ trợ thiết kế an toàn.\n\n"
                "• Không cần cài đặt Python, Node.js hoặc mở cửa sổ lệnh.\n"
                "• Mọi thao tác chỉnh sửa đều yêu cầu xác nhận ngoài model (trusted approval).\n"
                "• Mặc định bật khóa ghi (Write Lock) để bảo vệ bản vẽ an toàn tuyệt đối."
            )
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 13px; line-height: 1.5; color: #334155;")
            vbox.addWidget(title)
            vbox.addWidget(desc)
            vbox.addStretch()

        elif step_index == 1:
            title = QLabel("2. Kiểm tra kết nối Máy chủ & Web Portal")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            self.p2_status = QLabel("Trạng thái máy chủ: Đang kiểm tra...")
            self.p2_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #D97706;")
            self.p2_desc = QLabel(
                "Desktop Agent cần kết nối thành công tới Gateway WSS an toàn để hỗ trợ giao tiếp với ChatGPT."
            )
            self.p2_desc.setWordWrap(True)
            vbox.addWidget(title)
            vbox.addWidget(self.p2_status)
            vbox.addWidget(self.p2_desc)
            vbox.addStretch()

        elif step_index == 2:
            title = QLabel("3. Đăng nhập & Liên kết thiết bị qua Trình duyệt")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            self.p3_status = QLabel("Tài khoản: Chưa liên kết")
            self.p3_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #DC2626;")
            desc = QLabel(
                "Bấm 'Liên kết tài khoản' bên dưới. Trình duyệt web sẽ tự động mở trang đăng nhập Auth0 an toàn. "
                "Sau khi đăng nhập thành công, ứng dụng sẽ tự động kích hoạt thiết bị của bạn."
            )
            desc.setWordWrap(True)
            self.pair_btn = QPushButton("Mở trình duyệt để liên kết tài khoản")
            self.pair_btn.setStyleSheet("font-weight: 600; padding: 8px;")
            self.pair_btn.clicked.connect(self._open_browser_pairing)
            vbox.addWidget(title)
            vbox.addWidget(self.p3_status)
            vbox.addWidget(desc)
            vbox.addWidget(self.pair_btn)
            vbox.addStretch()

        elif step_index == 3:
            title = QLabel("4. Phát hiện ứng dụng AutoCAD")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            self.p4_status = QLabel("AutoCAD: Đang phát hiện...")
            self.p4_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #2563EB;")
            self.p4_desc = QLabel(
                "Sản phẩm hỗ trợ chính thức AutoCAD Full / Mechanical 2025–2026 (R25 Managed .NET Gateway).\n"
                "Hãy đảm bảo AutoCAD đã được cài đặt trên máy tính của bạn."
            )
            self.p4_desc.setWordWrap(True)
            vbox.addWidget(title)
            vbox.addWidget(self.p4_status)
            vbox.addWidget(self.p4_desc)
            vbox.addStretch()

        elif step_index == 4:
            title = QLabel("5. Kiểm tra & Cài đặt Managed Host R25")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            self.p5_status = QLabel("Managed Host: Đang kiểm tra...")
            self.p5_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #2563EB;")
            self.p5_desc = QLabel(
                "Managed Host R25 giúp AutoCAD giao tiếp chính xác với Desktop Agent mà không làm thay đổi cấu hình hệ thống.\n"
                "Lưu ý: Bản thử nghiệm hiện tại sử dụng Chứng chỉ Lab (Controlled Alpha Release)."
            )
            self.p5_desc.setWordWrap(True)
            self.install_host_btn = QPushButton("Cài đặt / Sửa Managed Host R25")
            self.install_host_btn.clicked.connect(self._install_host)
            vbox.addWidget(title)
            vbox.addWidget(self.p5_status)
            vbox.addWidget(self.p5_desc)
            vbox.addWidget(self.install_host_btn)
            vbox.addStretch()

        elif step_index == 5:
            title = QLabel("6. Kiểm tra kết nối Agent → Gateway → AutoCAD")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            self.p6_status = QLabel("Tổng quan kết nối: Đang kiểm tra...")
            self.p6_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #2563EB;")
            self.p6_desc = QLabel(
                "Hãy mở AutoCAD và mở một bản vẽ (ví dụ: Drawing A, B hoặc C).\n"
                "Trạng thái bản vẽ phải hiển thị 'Sẵn sàng' trước khi kết nối với ChatGPT."
            )
            self.p6_desc.setWordWrap(True)
            vbox.addWidget(title)
            vbox.addWidget(self.p6_status)
            vbox.addWidget(self.p6_desc)
            vbox.addStretch()

        elif step_index == 6:
            title = QLabel("7. Hoàn tất & Hướng dẫn kết nối ChatGPT")
            title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0F172A;")
            desc = QLabel(
                "Xin chúc mừng! Bạn đã hoàn tất các bước thiết lập cơ bản cho AutoCAD AI Connector MVP.\n\n"
                "Các bước tiếp theo để bắt đầu sử dụng:\n"
                "1. Mở trang Web Portal hoặc mở trang hướng dẫn kết nối ChatGPT.\n"
                "2. Thêm MCP Server connector vào ChatGPT theo địa chỉ Gateway.\n"
                "3. Sao chép mẫu câu lệnh (Starter Prompt) phân tích bản vẽ và gửi cho ChatGPT.\n"
                "4. ChatGPT sẽ quan sát bản vẽ, tạo Scene Graph và hiển thị kết quả trên Web Portal."
            )
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 13px; line-height: 1.5; color: #1E293B;")
            vbox.addWidget(title)
            vbox.addWidget(desc)
            vbox.addStretch()

        return page

    def update_from_state(self, state: AgentViewState) -> None:
        """Dynamically update wizard contents based on current state."""
        support = state.support_id or state.support_code or "—"
        self.support_id_label.setText(f"Mã hỗ trợ: {support}")

        # Page 2: Gateway / Server
        if state.server_connected:
            self.p2_status.setText("✓ Trạng thái máy chủ: Đã kết nối Gateway WSS")
            self.p2_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #16A34A;")
        else:
            self.p2_status.setText("✕ Trạng thái máy chủ: Chưa kết nối máy chủ")
            self.p2_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #DC2626;")

        # Page 3: Account / Device
        if state.device_name:
            self.p3_status.setText(f"✓ Thiết bị đã liên kết: {state.device_name}")
            self.p3_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #16A34A;")
        else:
            self.p3_status.setText("✕ Tài khoản: Chưa liên kết thiết bị")
            self.p3_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #DC2626;")

        # Page 4: AutoCAD detection
        if state.product or state.autocad_state != "Chưa kiểm tra":
            release = f" {state.release_year}" if state.release_year else ""
            self.p4_status.setText(f"✓ AutoCAD: {state.product or 'Phát hiện'}{release} ({state.autocad_state})")
            self.p4_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #16A34A;")
        else:
            self.p4_status.setText("! AutoCAD: Chưa mở AutoCAD")
            self.p4_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #D97706;")

        # Page 5: Managed Host
        if state.runtime_id == "managed_dotnet" and state.host_handshake_state == "connected":
            self.p5_status.setText(f"✓ Managed Host R25: Đã cài & hoạt động ({state.host_version or 'R25'})")
            self.p5_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #16A34A;")
        else:
            self.p5_status.setText("! Managed Host R25: Cần cài đặt hoặc kiểm tra lại bundle")
            self.p5_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #D97706;")

        # Page 6: End-to-end connection
        doc = state.document_name or "Chưa mở bản vẽ"
        if state.server_connected and state.document_name:
            self.p6_status.setText(f"✓ Sẵn sàng kết nối ChatGPT! Bản vẽ: {doc}")
            self.p6_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #16A34A;")
        else:
            self.p6_status.setText(f"! Đang chờ bản vẽ AutoCAD... (Hiện tại: {doc})")
            self.p6_status.setStyleSheet("font-weight: 600; font-size: 14px; color: #D97706;")

        self._update_step_ui()

    def _update_step_ui(self) -> None:
        idx = self.stack.currentIndex()
        titles = [
            "Chào mừng",
            "Kiểm tra máy chủ",
            "Liên kết tài khoản",
            "Phát hiện AutoCAD",
            "Cài đặt Managed Host",
            "Kiểm tra kết nối",
            "Hoàn tất & ChatGPT Guide",
        ]
        self.step_label.setText(f"Bước {idx + 1} / 7 — {titles[idx]}")

        self.prev_btn.setEnabled(idx > 0)
        self.next_btn.setVisible(idx < 6)
        self.finish_btn.setVisible(idx == 6)

    def _on_prev(self) -> None:
        idx = self.stack.currentIndex()
        if idx > 0:
            self.stack.setCurrentIndex(idx - 1)
            self._update_step_ui()

    def _on_next(self) -> None:
        idx = self.stack.currentIndex()
        if idx < 6:
            self.stack.setCurrentIndex(idx + 1)
            self._update_step_ui()

    def _on_retry(self) -> None:
        from ..state import AgentIntent
        self.core.handle_intent(AgentIntent.RETRY)
        QMessageBox.information(
            self,
            "Thử lại",
            "Đã thực hiện phát hiện và kiểm tra lại trạng thái kết nối.",
        )

    def _open_browser_pairing(self) -> None:
        from ..state import AgentIntent
        self.core.handle_intent(AgentIntent.RETRY)
        QMessageBox.information(
            self,
            "Liên kết tài khoản",
            "Đã gửi yêu cầu mở trình duyệt liên kết tài khoản qua Portal/OAuth.",
        )

    def _install_host(self) -> None:
        QMessageBox.information(
            self,
            "Cài đặt Managed Host R25",
            "Gói Managed Host R25 (Lab Release) đã được cấu hình trong đường dẫn Autodesk ApplicationPlugins.",
        )

    def _on_finish(self) -> None:
        self.finished_wizard.emit()
        self.accept()
