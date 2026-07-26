"""Desktop Agent entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import os
import webbrowser
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import AgentConfig, IdentityMode
from .core import AgentCore
from .credentials import DpapiCredentialProvider, EnvironmentCredentialProvider
from .executor import DrawingInfoExecutor
from .ledger import CommandLedger
from .pairing import (
    DeviceIdentityStore,
    PairedCredentialProvider,
    PairingApiClient,
)
from .runtime.autolisp_file_ipc import AutoLispFileIPCCadReadPort
from .runtime.broker import RuntimeBroker
from .runtime.managed_dotnet import ReloadingManagedDotNetCadReadPort
from .telemetry import TelemetryClient, TelemetryDrawingInfoExecutor


def build_core(config: AgentConfig, *, headless: bool) -> AgentCore:
    if config.identity_mode == IdentityMode.BROWSER_PAIRING:
        identity_store = DeviceIdentityStore(
            config.ledger_path.with_name("identity")
        )
        identity = identity_store.ensure()
        config = replace(config, device_id=identity.device_id).validate()
        credentials = PairedCredentialProvider(
            PairingApiClient(
                config.gateway_http_url,
                identity_store,
                portal_url=config.portal_url or config.gateway_http_url,
            )
        )
    else:
        credentials = (
            EnvironmentCredentialProvider()
            if headless
            else DpapiCredentialProvider(
                config.ledger_path.with_name("device.credential")
            )
        )
    ledger = CommandLedger(config.ledger_path)
    legacy_port = AutoLispFileIPCCadReadPort(
        package_version=config.package_version
    )
    adapters = [legacy_port]
    if config.managed_host_enabled:
        try:
            adapters.insert(
                0,
                ReloadingManagedDotNetCadReadPort.from_default_bootstrap(
                    agent_version=__version__,
                    expected_host_family="R25",
                ),
            )
        except OSError:
            # A missing per-user data directory must not stop the Agent.
            pass
    runtime_broker = RuntimeBroker(config, adapters)
    executor = DrawingInfoExecutor(
        legacy_port,
        config.package,
        __version__,
        runtime_broker=runtime_broker,
    )
    telemetry = TelemetryClient.from_env()
    if telemetry is not None:
        executor = TelemetryDrawingInfoExecutor(executor, telemetry)
    return AgentCore(config, credentials, ledger, executor)


async def pair_device(config: AgentConfig) -> None:
    if config.identity_mode != IdentityMode.BROWSER_PAIRING:
        raise RuntimeError(
            "Set AUTOCAD_AGENT_IDENTITY_MODE=browser_pairing before pairing"
        )
    store = DeviceIdentityStore(config.ledger_path.with_name("identity"))
    api = PairingApiClient(
        config.gateway_http_url,
        store,
        portal_url=config.portal_url or config.gateway_http_url,
    )
    enrollment = await api.start(config.device_name)
    print(f"Mã liên kết: {enrollment['user_code']}")
    print(f"Mở trình duyệt: {enrollment['confirmation_url']}")
    webbrowser.open(str(enrollment["confirmation_url"]))
    while True:
        status = await api.status(
            pairing_id=str(enrollment["pairing_id"]),
            polling_secret=str(enrollment["polling_secret"]),
        )
        state = status.get("state") or status.get("status")
        if state == "approved":
            break
        if state in {"denied", "expired", "completed"}:
            raise RuntimeError(f"Pairing stopped: {state}")
        await asyncio.sleep(2)
    await api.complete(
        pairing_id=str(enrollment["pairing_id"]),
        challenge=str(enrollment["challenge"]),
    )
    print("Liên kết thiết bị thành công.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kỹ Thuật Vàng AutoCAD Desktop Agent C1")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--pair",
        action="store_true",
        help="Liên kết thiết bị bằng trình duyệt rồi thoát.",
    )
    parser.add_argument("--package-self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.package_self_test:
        import websockets.asyncio.client  # noqa: F401

        return
    config = AgentConfig.from_env()
    if args.pair:
        asyncio.run(pair_device(config))
        return
    if config.identity_mode == IdentityMode.BROWSER_PAIRING:
        identity_store = DeviceIdentityStore(
            config.ledger_path.with_name("identity")
        )
        if not identity_store.is_paired():
            asyncio.run(pair_device(config))
    core = build_core(config, headless=args.headless)
    if args.headless:
        asyncio.run(core.run_forever())
        return
    from .ui.window import run_ui

    diagnostics_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Kythuatvang" / "AutoCADAgent" / "diagnostics"
    raise SystemExit(run_ui(core, diagnostics_dir))


if __name__ == "__main__":
    main()
