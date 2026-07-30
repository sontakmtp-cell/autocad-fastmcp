from __future__ import annotations

from autocad_desktop_agent.state import AgentIntent, AgentViewState, RuntimeState
from autocad_desktop_agent.ui.window import AgentWindow
from autocad_desktop_agent.ui.wizard import FirstRunWizard
from PySide6.QtWidgets import QMessageBox


class FakeCore:
    def __init__(self):
        self._state = AgentViewState(device_name="Máy Lab")
        self.intents = []
        self.callback = None

    @property
    def view_state(self):
        return self._state

    def subscribe(self, callback):
        self.callback = callback
        callback(self._state)

    def handle_intent(self, intent, diagnostics_target=None):
        self.intents.append((intent, diagnostics_target))


def test_window_maps_state_and_sends_typed_intents(qtbot, tmp_path):
    core = FakeCore()
    window = AgentWindow(core, tmp_path)
    qtbot.addWidget(window)
    window.show()
    core.callback(
        AgentViewState(
            device_name="PC Văn phòng",
            runtime_state=RuntimeState.PAUSED,
            server_connected=True,
            autocad_state="Đã kết nối",
            document_name="mat-bich.dwg",
            paused=True,
            hard_pause=True,
            write_lock_enabled=False,
            active_job_id="job-phase6",
            mismatch_reason="policy_mismatch",
            outcome_unknown=True,
            support_id="P6-1234",
        )
    )
    assert window.primary.text() == "Đã tạm dừng"
    assert "mat-bich.dwg" in window.values["document"].text()
    assert "▶️" in window.btn_pause.text()
    assert "🔓 Tắt" in window.values["write_lock"].text()
    assert "⏸️ Đang bật" in window.values["hard_pause"].text()
    assert "job-phase6" in window.values["active_job"].text()
    assert "Cần kiểm tra bản vẽ" in window.values["outcome"].text()
    assert window.values["support"].text() == "P6-1234"

    window.btn_recheck.click()
    window.btn_pause.click()
    assert [item[0] for item in core.intents] == [AgentIntent.RETRY, AgentIntent.RESUME]


def test_first_run_wizard_pages_and_navigation(qtbot):
    core = FakeCore()
    wizard = FirstRunWizard(core)
    qtbot.addWidget(wizard)
    wizard.show()

    assert wizard.stack.currentIndex() == 0
    assert "Bước 1 / 7" in wizard.step_label.text()

    wizard._on_next()
    assert wizard.stack.currentIndex() == 1
    assert "Bước 2 / 7" in wizard.step_label.text()

    wizard.update_from_state(
        AgentViewState(
            device_name="PC Tester",
            server_connected=True,
            product="AutoCAD Mechanical",
            release_year=2025,
            autocad_state="Sẵn sàng",
            document_name="drawingA.dwg",
            runtime_id="managed_dotnet",
            host_handshake_state="connected",
            host_version="2025.1",
        )
    )
    assert "✓ Trạng thái máy chủ: Đã kết nối Gateway WSS" in wizard.p2_status.text()

    wizard._on_prev()
    assert wizard.stack.currentIndex() == 0
    wizard.close()


def test_close_hides_to_tray(qtbot, tmp_path):
    window = AgentWindow(FakeCore(), tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.close()
    assert window.isVisible() is False


def test_exit_with_active_job_requires_confirmation(qtbot, tmp_path, monkeypatch):
    core = FakeCore()
    core._state = AgentViewState(
        device_name="Máy Lab",
        runtime_state=RuntimeState.BUSY_REMOTE,
        current_task="Đọc thông tin bản vẽ",
    )
    window = AgentWindow(core, tmp_path)
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    window._exit()
    assert core.intents == []

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr("autocad_desktop_agent.ui.window.QApplication.quit", lambda: None)
    window._exit()
    assert core.intents[-1][0] == AgentIntent.EXIT
