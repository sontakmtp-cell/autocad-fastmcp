"""Run the local public v1 Gateway."""

from __future__ import annotations

import uvicorn

from .app import GatewayConfig, create_app
from .backend import build_backend
from .services import GatewayServices


def main() -> None:
    config = GatewayConfig.from_env()
    services = GatewayServices(
        build_backend(),
        max_image_bytes=config.max_image_bytes,
    )
    uvicorn.run(
        create_app(services, config=config),
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
