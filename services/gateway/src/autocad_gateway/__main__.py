"""Run the local public v1 Gateway."""

from __future__ import annotations

import uvicorn
from autocad_contracts import MAX_WEBSOCKET_MESSAGE_BYTES

from .app import GatewayConfig, create_app
from .composition import build_human_auth, build_services


def main() -> None:
    config = GatewayConfig.from_env()
    services = build_services(config)
    uvicorn.run(
        create_app(services, config=config, auth=build_human_auth(config)),
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
        ws_max_size=MAX_WEBSOCKET_MESSAGE_BYTES,
        ws_max_queue=16,
    )


if __name__ == "__main__":
    main()
