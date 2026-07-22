from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable

from .agent import SimulatedAgent


_TERMINAL_DRAIN_SECONDS = 0.25


async def _run_agent(agent: SimulatedAgent, *, stop_after_terminal: bool) -> None:
    if not stop_after_terminal:
        await agent.run()
        return

    stop_requested = asyncio.Event()
    original_stop: Callable[[], None] = agent.stop

    def request_stop() -> None:
        stop_requested.set()

    # SimulatedAgent requests its stop only after the terminal frame has been sent.
    # Delay the actual socket close briefly so slower hosted Windows runners can
    # deliver and durably commit that frame before the close handshake begins.
    agent.stop = request_stop  # type: ignore[method-assign]
    run_task = asyncio.create_task(agent.run(stop_after_terminal=True))
    stop_task = asyncio.create_task(stop_requested.wait())
    try:
        done, _ = await asyncio.wait(
            {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if run_task in done:
            await run_task
            return
        await asyncio.sleep(_TERMINAL_DRAIN_SECONDS)
        original_stop()
        await run_task
    finally:
        agent.stop = original_stop  # type: ignore[method-assign]
        stop_task.cancel()
        if not run_task.done():
            original_stop()
            run_task.cancel()
        await asyncio.gather(run_task, stop_task, return_exceptions=True)


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
    agent = SimulatedAgent(
        url=args.url,
        device_id=args.device_id,
        token=args.token,
        scenario=args.scenario,
        fixture_variant=args.fixture_variant,
        max_reconnects=args.max_reconnects,
    )
    asyncio.run(_run_agent(agent, stop_after_terminal=args.stop_after_terminal))


if __name__ == "__main__":
    main()
