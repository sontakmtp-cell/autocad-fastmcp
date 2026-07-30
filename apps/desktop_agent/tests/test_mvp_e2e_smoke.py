"""End-to-end MVP smoke test for AutoCAD AI Connector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import ezdxf

from autocad_desktop_agent.state import AgentIntent, AgentViewState, RuntimeState
from autocad_desktop_agent.ui.window import AgentWindow
from autocad_desktop_agent.ui.wizard import FirstRunWizard
from autocad_desktop_agent.diagnostics import export_diagnostics
from autocad_mcp.phase10_projection import project_ezdxf_entities


class FakeCore:
    def __init__(self) -> None:
        self._state = AgentViewState(
            device_name="PC Tester MVP",
            server_connected=True,
            product="AutoCAD Mechanical",
            release_year=2025,
            autocad_state="Sẵn sàng",
            document_name="phase10-drawing-a.dwg",
            runtime_id="managed_dotnet",
            host_handshake_state="connected",
            host_version="2025.1",
            write_lock_enabled=True,
            hard_pause=False,
            paused=False,
        )
        self.intents: list[tuple[AgentIntent, Path | None]] = []

    @property
    def view_state(self) -> AgentViewState:
        return self._state

    def subscribe(self, callback: any) -> None:
        callback(self._state)

    def handle_intent(self, intent: AgentIntent, diagnostics_target: Path | None = None) -> None:
        self.intents.append((intent, diagnostics_target))


def test_mvp_wizard_onboarding_flow(qtbot):
    core = FakeCore()
    wizard = FirstRunWizard(core)
    qtbot.addWidget(wizard)
    wizard.show()

    # Step 1 -> 7 transition
    assert wizard.stack.currentIndex() == 0
    for _ in range(6):
        wizard._on_next()
    assert wizard.stack.currentIndex() == 6
    assert wizard.finish_btn.isVisible()

    wizard.update_from_state(core.view_state)
    assert "Sẵn sàng" in wizard.p6_status.text()
    wizard.close()


def test_mvp_dashboard_rendering_and_quick_actions(qtbot, tmp_path):
    core = FakeCore()
    window = AgentWindow(core, tmp_path)
    qtbot.addWidget(window)
    window.show()

    assert "✓ Online" in window.values["server"].text()
    assert "🔒 Bật" in window.values["write_lock"].text()
    assert "✓ AutoCAD Mechanical 2025" in window.values["autocad"].text()

    # Test quick action intents
    window.btn_recheck.click()
    window.btn_toggle_write_lock.click()
    window.btn_pause.click()

    intents = [item[0] for item in core.intents]
    assert AgentIntent.RETRY in intents
    assert AgentIntent.DISABLE_WRITE_LOCK in intents
    assert AgentIntent.PAUSE in intents


def test_mvp_drawing_a_b_c_scene_observation():
    # Drawing A: Plate with repeated holes
    doc_a = ezdxf.new("R2018")
    space_a = doc_a.modelspace()
    space_a.add_lwpolyline([(0, 0), (120, 0), (120, 80), (0, 80)], close=True)
    for center in [(20, 20), (100, 20), (20, 60), (100, 60)]:
        space_a.add_circle(center, 5)

    projected_a = project_ezdxf_entities(list(space_a))
    circles_a = [e for e in projected_a if e["type"] == "CIRCLE"]
    assert len(circles_a) == 4

    # Drawing B: Slot & Concentric Group
    doc_b = ezdxf.new("R2018")
    space_b = doc_b.modelspace()
    space_b.add_circle((50, 0), 3)
    space_b.add_circle((50, 0), 6)

    projected_b = project_ezdxf_entities(list(space_b))
    concentric_b = [e for e in projected_b if e["type"] == "CIRCLE"]
    assert len(concentric_b) == 2
    assert concentric_b[0]["geometry"]["center"] == concentric_b[1]["geometry"]["center"]

    # Drawing C: Cleanup Anomalies
    doc_c = ezdxf.new("R2018")
    space_c = doc_c.modelspace()
    space_c.add_line((0, 0), (10, 0))
    space_c.add_line((10, 0), (0, 0))  # Duplicate reversed

    projected_c = project_ezdxf_entities(list(space_c))
    lines_c = [e for e in projected_c if e["type"] == "LINE"]
    assert len(lines_c) == 2


def test_mvp_redacted_diagnostics_no_secret_leak(tmp_path):
    target = tmp_path / "diagnostics-redacted.json"
    values = {
        "support_id": "SUP-MVP-99",
        "write_lock_enabled": True,
        "product": "AutoCAD Mechanical 2025",
        "secret_token": "SHOULD_NOT_BE_EXPORTED",
    }
    export_diagnostics(target, device_id="device123456", values=values)

    content = target.read_text(encoding="utf-8").lower()
    assert "secret_token" not in content
    assert "should_not_be_exported" not in content
    assert "sup-mvp-99" in content
