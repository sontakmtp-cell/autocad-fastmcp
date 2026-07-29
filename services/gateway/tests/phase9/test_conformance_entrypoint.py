import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_conformance_entrypoint_creates_clean_runner_cache_parent(
    monkeypatch, tmp_path
):
    script = (
        Path(__file__).resolve().parents[4]
        / "scripts"
        / "test-phase9-conformance.py"
    )
    spec = importlib.util.spec_from_file_location("phase9_conformance", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path

    def fake_run(command, *, cwd, env, check):
        assert command
        assert cwd == tmp_path
        assert check is False
        assert (tmp_path / ".pytest_cache").is_dir()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main() == 0
