from __future__ import annotations

import asyncio
import copy
import hashlib

import pytest
import httpx
from asgi_lifespan import LifespanManager
from autocad_contracts import (
    AckMessage,
    ResultMessage,
    canonical_capability_hash,
    canonical_package_manifest_hash,
    parse_agent_message,
)

from autocad_gateway.app import GatewayConfig, create_app
from autocad_gateway.composition import build_human_auth, build_services
from autocad_gateway.contracts import (
    CadListDevicesInput,
    CadObserveInputDurable,
    CadQueryInput,
    PHASE4_CONTRACT_VERSION,
    Principal,
)
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.application.job_service import DurableJobService
from autocad_gateway.infrastructure.agent_transport.authenticator import LabDeviceAuthenticator
from autocad_gateway.infrastructure.agent_transport.connection_registry import AgentConnection, ConnectionRegistry
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.services import GatewayError


PACKAGE = {
    "package_id": "autocad.lisp.drawing_info",
    "version": "3.3-c1",
    "sha256": "a" * 64,
}


@pytest.mark.parametrize(
    "code",
    ["paused_by_user", "package_mismatch", "autocad_busy", "modal_dialog_active"],
)
def test_phase4_safe_agent_errors_remain_typed(code):
    public_code, summary = DurableJobService._safe_agent_error(code)
    assert public_code == code
    assert summary


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, value):
        self.messages.append(parse_agent_message(value))

    async def close(self, **kwargs):
        return None


def config(tmp_path, **changes):
    values = dict(
        profile="phase4_c1",
        db_path=str(tmp_path / "phase4.db"),
        fixture_tokens=(("device-lab", "credential"),),
        fixture_owner_subject="auth0|lab-user",
        oauth_issuer="https://tenant.example/",
        oauth_audience="https://cad.example",
        oauth_jwks_uri="https://tenant.example/.well-known/jwks.json",
        public_origin="https://cad.example",
        required_package_id=PACKAGE["package_id"],
        required_package_version=PACKAGE["version"],
        required_package_sha256=PACKAGE["sha256"],
        device_display_name="PC Văn phòng",
    )
    values.update(changes)
    return GatewayConfig(**values)


def test_phase4_profile_fails_closed(tmp_path):
    config(tmp_path).validate()
    with pytest.raises(ValueError, match="exactly one"):
        config(tmp_path, fixture_tokens=(("a", "x"), ("b", "y"))).validate()
    with pytest.raises(ValueError, match="write_disabled"):
        config(tmp_path, write_disabled=False).validate()
    with pytest.raises(ValueError, match="package SHA-256"):
        config(tmp_path, required_package_sha256="BAD").validate()
    with pytest.raises(ValueError, match="public origin"):
        config(tmp_path, public_origin="https://cad.example/not-an-origin").validate()


@pytest.mark.asyncio
async def test_phase4_oauth_metadata_and_runtime_challenge(tmp_path):
    cfg = config(tmp_path, allowed_hosts=("testserver",)).validate()
    app = create_app(build_services(cfg), build_human_auth(cfg), config=cfg)
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200
            assert metadata.json() == {
                "resource": "https://cad.example/mcp",
                "authorization_servers": ["https://tenant.example/"],
                "scopes_supported": ["autocad.read"],
                "bearer_methods_supported": ["header"],
                "resource_name": "Kỹ Thuật Vàng AutoCAD",
            }
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == (
                'Bearer resource_metadata="https://cad.example/'
                '.well-known/oauth-protected-resource/mcp"'
            )


@pytest.mark.asyncio
async def test_phase4_summary_evidence_and_query_fail_closed(tmp_path):
    cfg = config(tmp_path).validate()
    registry = ConnectionRegistry(stale_after_seconds=45)
    service = DurableGatewayServices(
        SqliteDatabase(cfg.db_path),
        registry,
        device_tokens=dict(cfg.fixture_tokens),
        owner_subject=cfg.fixture_owner_subject,
        profile="phase4_c1",
        agent_authenticator=LabDeviceAuthenticator(dict(cfg.fixture_tokens)),
        required_package=PACKAGE,
        display_name=cfg.device_display_name,
        request_wait_timeout_seconds=2,
    )
    await service.initialize()
    socket = Socket()
    connection = AgentConnection(
        device_id="device-lab",
        session_id="session-1",
        websocket=socket,
        protocol_version="cad.agent/1",
        capabilities=("observe",),
        capability_hash=canonical_capability_hash(["observe"]),
        agent_version="0.1.0",
        runtime_state="online_idle",
        document_name="mat-bich.dwg",
        packages=(PACKAGE,),
        package_manifest_hash=canonical_package_manifest_hash([PACKAGE]),
    )
    await registry.add(connection)
    await service.on_agent_connected(connection)
    principal = Principal(subject=cfg.fixture_owner_subject, scopes=("autocad.read",))
    listed = await service.list_devices(CadListDevicesInput(), principal, "corr-list")
    assert listed.contract_version == PHASE4_CONTRACT_VERSION
    assert listed.devices[0].agent_version == "0.1.0"
    assert listed.devices[0].document_name == "mat-bich.dwg"
    hidden = await service.list_devices(
        CadListDevicesInput(),
        Principal(subject="auth0|someone-else", scopes=("autocad.read",)),
        "corr-hidden",
    )
    assert hidden.contract_version == PHASE4_CONTRACT_VERSION
    assert hidden.devices == []

    task = asyncio.create_task(
        service.observe(
            CadObserveInputDurable(device_id="device-lab", idempotency_key="idem-1"),
            principal,
            "corr-observe",
        )
    )
    for _ in range(50):
        if socket.messages:
            break
        await asyncio.sleep(0.01)
    command = socket.messages[-1]
    await service.job_service.handle_message(
        connection,
        AckMessage(
            session_id=connection.session_id,
            device_id=connection.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=1,
            status="accepted",
            idempotency_key=command.idempotency_key,
            payload_hash=command.payload_hash,
        ),
    )
    snapshot = {
        "snapshot_id": "snapshot-c1",
        "document_revision": hashlib.sha256(b"summary").hexdigest(),
        "observation_level": "summary",
        "drawing": {
            "document_name": "mat-bich.dwg",
            "entity_count": 42,
            "layers": ["0"],
            "layer_count": 1,
            "truncated": False,
            "dispatcher_version": PACKAGE["version"],
            "package_id": PACKAGE["package_id"],
            "package_version": PACKAGE["version"],
        },
        "entity_summary": {"entity_count": 42, "detail_available": False},
        "entities": [],
        "revision_evidence": {
            "revision_schema": "cad.revision/1",
            "revision_strength": "summary_only",
            "commit_safe": False,
        },
    }
    await service.job_service.handle_message(
        connection,
        ResultMessage(
            session_id=connection.session_id,
            device_id=connection.device_id,
            job_id=command.job_id,
            command_id=command.command_id,
            sequence=2,
            status="succeeded",
            payload_hash=command.payload_hash,
            result={
                "snapshot": snapshot,
                "execution_evidence": {
                    "agent_version": "0.1.0",
                    "runtime_state": "online_idle",
                    "package": PACKAGE,
                },
            },
        ),
    )
    observed = await task
    assert observed.contract_version == PHASE4_CONTRACT_VERSION
    assert observed.entity_count == 42
    assert observed.revision_evidence.commit_safe is False
    assert observed.execution_evidence.package.sha256 == PACKAGE["sha256"]
    leaked = copy.deepcopy(snapshot)
    leaked["drawing"]["document_name"] = r"C:\\Sensitive\\mat-bich.dwg"
    assert service.job_service._validate_c1_observation(
        {"snapshot": leaked, "execution_evidence": {
            "agent_version": "0.1.0",
            "runtime_state": "online_idle",
            "package": PACKAGE,
        }},
        leaked,
    ) == "backend_error"
    managed_snapshot = copy.deepcopy(snapshot)
    managed_snapshot["drawing"] = {
        key: value
        for key, value in managed_snapshot["drawing"].items()
        if key not in {"dispatcher_version", "package_id", "package_version"}
    }
    managed_result = {
        "snapshot": managed_snapshot,
        "execution_evidence": {
            "agent_version": "0.1.0",
            "runtime_state": "online_idle",
            "package": PACKAGE,
            "runtime": {
                "id": "managed_dotnet",
                "role": "primary",
                "host_family": "R25",
                "host_version": "0.1.0",
                "framework": ".NET 8",
                "package_id": "autocad.mcp.managed_host",
                "package_version": "0.1.0",
                "package_hash": "a" * 64,
            },
            "degraded": False,
            "degradation_reason": None,
        },
    }
    assert (
        service.job_service._validate_c1_observation(
            managed_result, managed_snapshot
        )
        is None
    )
    with pytest.raises(GatewayError) as captured:
        await service.query(CadQueryInput(snapshot_id="snapshot-c1"), principal, "corr-query")
    assert captured.value.code == "capability_missing"
    await service.shutdown()


@pytest.mark.asyncio
async def test_package_mismatch_marks_device_incompatible(tmp_path):
    cfg = config(tmp_path).validate()
    registry = ConnectionRegistry()
    service = DurableGatewayServices(
        SqliteDatabase(cfg.db_path),
        registry,
        device_tokens=dict(cfg.fixture_tokens),
        owner_subject=cfg.fixture_owner_subject,
        profile="phase4_c1",
        agent_authenticator=LabDeviceAuthenticator(dict(cfg.fixture_tokens)),
        required_package=PACKAGE,
    )
    await service.initialize()
    connection = AgentConnection(
        device_id="device-lab",
        session_id="session-bad",
        websocket=Socket(),
        protocol_version="cad.agent/1",
        capabilities=("observe",),
        capability_hash=canonical_capability_hash(["observe"]),
        packages=({**PACKAGE, "sha256": "b" * 64},),
    )
    with pytest.raises(Exception, match="package_mismatch"):
        await service.on_agent_connected(connection)
    device = await service.repository.get_device(cfg.fixture_owner_subject, "device-lab")
    assert device["status"] == "incompatible"
    listed = await service.list_devices(
        CadListDevicesInput(),
        Principal(subject=cfg.fixture_owner_subject, scopes=("autocad.read",)),
        "corr-incompatible",
    )
    assert listed.devices[0].status == "incompatible"
    await service.shutdown()
