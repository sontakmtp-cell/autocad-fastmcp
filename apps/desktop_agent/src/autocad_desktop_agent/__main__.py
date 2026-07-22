"""Desktop Agent entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from . import __version__
from .config import AgentConfig
from .core import AgentCore
from .credentials import DpapiCredentialProvider, EnvironmentCredentialProvider
from .executor import DrawingInfoExecutor, SafeFileIPCCadReadPort
from .ledger import CommandLedger


def build_core(config: AgentConfig, *, headless: bool) -> AgentCore:
    ledger = CommandLedger(config.ledger_path)
    credentials = (
        EnvironmentCredentialProvider()
        if headless
        else DpapiCredentialProvider(config.ledger_path.with_name("device.credential"))
    )
    executor = DrawingInfoExecutor(SafeFileIPCCadReadPort(), config.package, __version__)
    return AgentCore(config, credentials, ledger, executor)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kỹ Thuật Vàng AutoCAD Desktop Agent C1")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    config = AgentConfig.from_env()
    core = build_core(config, headless=args.headless)
    if args.headless:
        asyncio.run(core.run_forever())
        return
    from .ui.window import run_ui

    diagnostics_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Kythuatvang" / "AutoCADAgent" / "diagnostics"
    raise SystemExit(run_ui(core, diagnostics_dir))


if __name__ == "__main__":
    main()
