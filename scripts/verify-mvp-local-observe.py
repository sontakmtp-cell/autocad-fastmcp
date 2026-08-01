"""Local MVP verification: drive one real observe through the live Agent."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from pathlib import Path

import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[1]
DEVICE_ID = "device-lab"
DEVICE_CREDENTIAL = "mvp-local-lab-credential-2026"
DB_PATH = Path(os.environ["LOCALAPPDATA"]) / "Kythuatvang" / "AutoCADGateway" / "mvp-local.sqlite3"


def configure_env() -> None:
    lisp_hash = hashlib.sha256(
        (REPO_ROOT / "lisp-code" / "mcp_dispatch.lsp").read_bytes()
    ).hexdigest()
    os.environ["AUTOCAD_MCP_GATEWAY_PROFILE"] = "phase4_c1"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_HOST"] = "127.0.0.1"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_PORT"] = "8000"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_PATH"] = "/mcp"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_STATELESS_HTTP"] = "0"
    os.environ["AUTOCAD_MCP_PUBLIC_V1_ALLOWED_HOSTS"] = "127.0.0.1:8000;localhost:8000"
    os.environ["AUTOCAD_MCP_PHASE4_DB_PATH"] = str(DB_PATH)
    os.environ["AUTOCAD_MCP_PHASE4_DEVICE_ID"] = DEVICE_ID
    os.environ["AUTOCAD_MCP_PHASE4_DEVICE_CREDENTIAL"] = DEVICE_CREDENTIAL
    os.environ["AUTOCAD_MCP_PHASE4_OWNER_SUBJECT"] = "auth0|lab-owner"
    os.environ["AUTOCAD_MCP_PHASE4_DEVICE_DISPLAY_NAME"] = "May AutoCAD Lab"
    os.environ["AUTOCAD_MCP_PHASE4_WRITE_DISABLED"] = "1"
    os.environ["AUTOCAD_MCP_PHASE4_OAUTH_ISSUER"] = "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/"
    os.environ["AUTOCAD_MCP_PHASE4_OAUTH_AUDIENCE"] = "https://cad.kythuatvang.com/mcp"
    os.environ["AUTOCAD_MCP_PHASE4_OAUTH_JWKS_URI"] = (
        "https://dev-fmth5j5hp2e5sk3s.us.auth0.com/.well-known/jwks.json"
    )
    os.environ["AUTOCAD_MCP_PHASE4_PUBLIC_ORIGIN"] = "https://cad.kythuatvang.com"
    os.environ["AUTOCAD_MCP_PHASE4_PACKAGE_ID"] = "autocad.lisp.drawing_info"
    os.environ["AUTOCAD_MCP_PHASE4_PACKAGE_VERSION"] = "3.3-c1"
    os.environ["AUTOCAD_MCP_PHASE4_PACKAGE_SHA256"] = lisp_hash


async def main() -> None:
    configure_env()
    sys.path.insert(0, str(REPO_ROOT / "services" / "gateway" / "src"))
    from autocad_gateway.app import GatewayConfig, create_app
    from autocad_gateway.composition import build_human_auth, build_services
    from autocad_gateway.contracts import CadObserveInputDurable, Principal

    config = GatewayConfig.from_env().validate()
    services = build_services(config)
    await services.initialize()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(services, config=config, auth=build_human_auth(config)),
            host="127.0.0.1",
            port=8000,
            log_level="warning",
            access_log=False,
        )
    )
    serve_task = asyncio.create_task(server.serve())

    # Wait for the Desktop Agent to connect to this process.
    for _ in range(120):
        devices = await services.list_devices(
            __import__("autocad_gateway.contracts", fromlist=["CadListDevicesInput"]).CadListDevicesInput(),
            Principal(subject="auth0|lab-owner", scopes=("autocad.read",)),
            "corr-verify",
        )
        online = [
            device
            for device in devices.devices
            if getattr(device, "status", None) == "online"
            or getattr(device, "state", None) == "online"
        ]
        if online:
            break
        await asyncio.sleep(1)
    else:
        print("ERROR: Desktop Agent did not connect within 120s")
        server.should_exit = True
        await serve_task
        return

    print("Agent online:", online[0].model_dump(exclude_none=True) if hasattr(online[0], "model_dump") else online[0])
    principal = Principal(subject="auth0|lab-owner", scopes=("autocad.read",))
    result = await asyncio.wait_for(
        services.observe(
            CadObserveInputDurable(
                device_id=DEVICE_ID,
                idempotency_key=f"mvp-verify-{uuid.uuid4().hex[:12]}",
            ),
            principal,
            "corr-observe-verify",
        ),
        timeout=120,
    )
    print("OBSERVE RESULT:", result.model_dump(mode="json", exclude_none=True))
    server.should_exit = True
    await serve_task


if __name__ == "__main__":
    asyncio.run(main())
