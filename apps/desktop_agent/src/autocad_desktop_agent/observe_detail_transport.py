"""Transport sizing for bounded read-only detail observations.

The managed host pages entity reads below its own pipe-frame limit, but the final
observation aggregates those pages.  A few thousand entity projections can exceed
the original 1 MiB Agent envelope limit.  The Gateway still applies a stricter
large-frame admission check so payloads above the legacy limit are accepted only
for read-only detail observation results.
"""

from __future__ import annotations

import autocad_contracts
import autocad_contracts.agent_protocol as agent_protocol

DETAIL_OBSERVE_MESSAGE_BYTES = 16 * 1024 * 1024


def install_detail_observe_transport_limit() -> None:
    """Raise only this Agent process' transport ceiling for detail result frames."""

    agent_protocol.MAX_WEBSOCKET_MESSAGE_BYTES = DETAIL_OBSERVE_MESSAGE_BYTES
    # ``autocad_contracts`` re-exports the constant by value. Keep that public
    # module attribute aligned for callers that import it after Agent startup.
    autocad_contracts.MAX_WEBSOCKET_MESSAGE_BYTES = DETAIL_OBSERVE_MESSAGE_BYTES
