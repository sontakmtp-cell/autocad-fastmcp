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
from .executor import DrawingInfoExecutor
from .ledger import CommandLedger
from .runtime.autolisp_file_ipc import AutoLispFileIPCCadReadPort
from .runtime.broker import RuntimeBroker
from .runtime.managed_dotnet import ManagedDotNetCadReadPort


def build_core(config: AgentConfig, *, headless: bool) -> AgentCore:
    ledger = CommandLedger(config.ledger_path)
    credentials = (
        EnvironmentCredentialProvider()
        if headless
        else DpapiCredentialProvider(config.ledger_path.with_name("device.credential"))
    )
    legacy_port = AutoLispFileIPCCadReadPort(
        package_version=config.package_version
    )
    adapters = [legacy_port]
    if config.managed_host_enabled:
        try:
            adapters.insert(
                0,
                ManagedDotNetCadReadPort.from_default_bootstrap(
                    agent_version=__version__,
                    expected_host_family="R25",
                ),
            )
        except (OSError, ValueError):
            # The broker reports plugin_required/degraded state without making
            # the Agent process fail when the lab Host is absent or not loaded.
            pass
    runtime_broker = RuntimeBroker(config, adapters)
    executor = DrawingInfoExecutor(
        legacy_port,
        config.package,
        __version__,
        runtime_broker=runtime_broker,
    )
    return AgentCore(config, credentials, ledger, executor)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kỹ Thuật Vàng AutoCAD Desktop Agent C1")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--package-self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.package_self_test:
        import websockets.asyncio.client  # noqa: F401

        return
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
