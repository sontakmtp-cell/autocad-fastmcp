from __future__ import annotations

import hashlib
import hmac

from autocad_contracts import canonical_json

from autocad_desktop_agent.runtime.autolisp_file_ipc import AutoLispFileIPCCadReadPort
from autocad_desktop_agent.runtime.contracts import RuntimeProbe
from autocad_desktop_agent.runtime.managed_dotnet import ManagedDotNetCadReadPort


class Phase10HostTransport:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def request(self, request: dict) -> dict:
        self.requests.append(request)
        payload = request["payload"]
        if request["message_type"] == "handshake":
            nonce = payload["session_nonce"]
            result = {
                "selected_protocol": "cad.host/1",
                "host_family": "R25",
                "host_version": "0.10.0",
                "package_id": "autocad.managed_host.r25",
                "package_version": "0.10.0",
                "package_hash": f"sha256:{'a' * 64}",
                "session_proof": hmac.new(
                    b"s" * 32,
                    f"cad.host/1\n{request['session_id']}\n{nonce}".encode(),
                    hashlib.sha256,
                ).hexdigest(),
                "product": "AutoCAD Mechanical",
                "edition": "full",
                "release_year": 2025,
                "series": "R25.0",
                "active_document_id": "doc-1",
                "capabilities": [
                    "host.health",
                    "observe.summary",
                    "entity.snapshot.v2",
                    "entity.geometry.line/1",
                    "entity.geometry.circle/1",
                    "entity.geometry.polyline/1",
                    "entity.geometry.arc/1",
                ],
            }
            message_type = "handshake_result"
        else:
            result = {
                "status": "succeeded",
                "operation_id": "entity.snapshot.page",
                "runtime_evidence": {
                    "runtime_id": "managed_dotnet",
                    "runtime_role": "primary",
                    "host_family": "R25",
                    "host_version": "0.10.0",
                },
                "result": {
                    "document_id": "doc-1",
                    "revision": {"revision": 7},
                    "next_cursor": None,
                    "source_capabilities": ["entity.geometry.arc/1"],
                    "entities": [{
                        "handle": "A1",
                        "type": "ARC",
                        "geometry_status": "exact",
                        "geometry_reason": None,
                        "source_capabilities": ["entity.geometry.arc/1"],
                        "geometry": {
                            "center": [0.0, 0.0],
                            "radius": 2.0,
                            "start_angle": 0.0,
                            "end_angle": 1.0,
                            "elevation": 0.0,
                            "normal": [0.0, 0.0, 1.0],
                        },
                    }],
                },
            }
            message_type = "result"
        return {
            "protocol_version": "cad.host/1",
            "message_type": message_type,
            "session_id": request["session_id"],
            "command_id": request["command_id"],
            "sequence": request["sequence"],
            "deadline_at": request["deadline_at"],
            "payload_hash": hashlib.sha256(canonical_json(result).encode()).hexdigest(),
            "payload": result,
        }


async def test_agent_requests_and_forwards_tier_a_projection_up_to_lab_limit():
    transport = Phase10HostTransport()
    adapter = ManagedDotNetCadReadPort(
        transport,
        session_secret=b"s" * 32,
        agent_version="0.10.0",
        expected_host_family="R25",
    )

    result = await adapter.entity_snapshot(limit=5_000, expected_revision=7)

    assert result.ok is True
    assert result.payload["entities"][0]["geometry_status"] == "exact"
    assert result.payload["source_capabilities"] == ["entity.geometry.arc/1"]
    manifest = adapter.manifest(
        RuntimeProbe(
            runtime_id="managed_dotnet",
            available=True,
            product="AutoCAD Mechanical",
            edition="full",
            release_year=2025,
            series="R25.0",
        )
    )
    assert set(manifest.cad_products[0].capabilities) >= {
        "entity.geometry.line/1",
        "entity.geometry.circle/1",
        "entity.geometry.polyline/1",
        "entity.geometry.arc/1",
    }
    arguments = transport.requests[-1]["payload"]["arguments"]
    assert arguments["types"] == ["LINE", "CIRCLE", "LWPOLYLINE", "ARC"]
    assert arguments["expected_revision"] == 7


async def test_agent_rejects_projection_above_lab_cap():
    adapter = ManagedDotNetCadReadPort(
        Phase10HostTransport(),
        session_secret=b"s" * 32,
        agent_version="0.10.0",
    )

    result = await adapter.entity_snapshot(limit=5_001)

    assert result.ok is False
    assert result.error_code == "capability_missing"


def test_lt_manifest_does_not_claim_phase10_geometry_or_write():
    manifest = AutoLispFileIPCCadReadPort().manifest(
        RuntimeProbe(
            runtime_id="autolisp_file_ipc",
            available=True,
            product="AutoCAD LT",
            edition="lt",
            release_year=2025,
        )
    )

    capabilities = set(manifest.cad_products[0].capabilities)
    assert capabilities == {"observe.summary"}
    assert not any(capability.startswith("entity.geometry.") for capability in capabilities)
    assert not any("write" in capability or "commit" in capability for capability in capabilities)
