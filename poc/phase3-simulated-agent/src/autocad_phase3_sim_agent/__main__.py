from __future__ import annotations

import argparse
import asyncio

from .agent import SimulatedAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one outbound Phase 3 simulated Agent")
    parser.add_argument("--url", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--scenario", default="success")
    args = parser.parse_args()
    asyncio.run(
        SimulatedAgent(
            url=args.url,
            device_id=args.device_id,
            token=args.token,
            scenario=args.scenario,
        ).run()
    )


if __name__ == "__main__":
    main()
