from __future__ import annotations

from autocad_desktop_agent.state import AgentIntent, AgentViewState, RuntimeState
from autocad_desktop_agent.ui.window import AgentWindow
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
        )
    )
    assert window.primary.text() == "Đã tạm dừng"
    assert window.values["document"].text() == "mat-bich.dwg"
    assert window.pause_button.text() == "Tiếp tục"
    window.retry_button.click()
    window.pause_button.click()
    assert [item[0] for item in core.intents] == [AgentIntent.RETRY, AgentIntent.RESUME]


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
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)
    window._exit()
    assert core.intents == []

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    monkeypatch.setattr("autocad_desktop_agent.ui.window.QApplication.quit", lambda: None)
    window._exit()
    assert core.intents[-1][0] == AgentIntent.EXIT
