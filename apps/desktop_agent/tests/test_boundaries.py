from pathlib import Path


def test_ui_does_not_import_network_com_or_file_ipc():
    root = Path(__file__).parents[1] / "src" / "autocad_desktop_agent" / "ui"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for forbidden in ("websockets", "win32com", "FileIPCBackend", "SafeFileIPCBackend"):
        assert forbidden not in text


def test_command_router_only_receives_narrow_executor():
    core = (Path(__file__).parents[1] / "src" / "autocad_desktop_agent" / "core.py").read_text(encoding="utf-8")
    assert "SafeFileIPCBackend" not in core
    assert "execute_lisp" not in core
