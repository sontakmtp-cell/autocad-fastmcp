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
    parser.add_argument("--fixture-variant", type=int, default=0)
    parser.add_argument("--max-reconnects", type=int, default=8)
    parser.add_argument("--stop-after-terminal", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        SimulatedAgent(
            url=args.url,
            device_id=args.device_id,
            token=args.token,
            scenario=args.scenario,
            fixture_variant=args.fixture_variant,
            max_reconnects=args.max_reconnects,
        ).run(stop_after_terminal=args.stop_after_terminal)
    )


if __name__ == "__main__":
    main()
