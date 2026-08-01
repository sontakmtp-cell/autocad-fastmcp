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
from cad_core.scene import project_entity

from autocad_gateway.app import GatewayConfig, create_app
from autocad_gateway.composition import build_human_auth, build_services
from autocad_gateway.contracts import (
    CadListDevicesInput,
    CadObserveInputDurable,
    CadQueryInput,
    PHASE4_CONTRACT_VERSION,
    Principal,
    RevisionEvidence,
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


@pytest.mark.parametrize(
    ("entity_type", "geometry"),
    [
        (
            "LINE",
            {
                "start": [0.0, 0.0],
                "end": [10.0, 0.0],
                "start_elevation": 0.0,
                "end_elevation": 0.0,
            },
        ),
        (
            "CIRCLE",
            {
                "center": [5.0, 5.0],
                "radius": 2.0,
                "elevation": 0.0,
                "normal": [0.0, 0.0, 1.0],
            },
        ),
        (
            "LWPOLYLINE",
            {
                "points": [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0]],
                "bulges": [0.0, 1.0, 0.0],
                "closed": True,
                "elevation": 0.0,
                "normal": [0.0, 0.0, 1.0],
            },
        ),
        (
            "ARC",
            {
                "center": [5.0, 5.0],
                "radius": 2.0,
                "start_angle_radians": 0.0,
                "end_angle_radians": 3.141592653589793,
                "elevation": 0.0,
                "normal": [0.0, 0.0, 1.0],
            },
        ),
    ],
)
def test_c1_detail_accepts_exact_managed_host_geometry(entity_type, geometry):
    capability = {
        "LINE": "entity.geometry.line/1",
        "CIRCLE": "entity.geometry.circle/1",
        "LWPOLYLINE": "entity.geometry.polyline/1",
        "ARC": "entity.geometry.arc/1",
    }[entity_type]
    entity = {
        "entity_id": "1A",
        "entity_type": entity_type,
        "layer": "0",
        "space": "model",
        "bounds": {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 0.0]},
        "geometry": geometry,
        "geometry_status": "exact",
        "geometry_reason": None,
        "geometry_truncated": False,
        "fingerprint": f"sha256:{'b' * 64}",
        "source_runtime": "managed_dotnet",
        "source_capabilities": [capability],
    }

    assert DurableJobService._valid_c1_detail_entity(entity)


@pytest.mark.parametrize(
    "geometry",
    [
        {
            "points": [[0.0, 0.0], [1.0, 0.0]],
            "bulges": [0.0],
            "closed": False,
            "elevation": 0.0,
            "normal": [0.0, 0.0, 1.0],
        },
        {
            "center": [0.0, 0.0],
            "radius": float("nan"),
            "elevation": 0.0,
            "normal": [0.0, 0.0, 1.0],
        },
        {
            "center": [0.0, 0.0],
            "radius": 1.0,
            "elevation": 0.0,
            "normal": [0.0, 0.0, 1.0],
            "unexpected": True,
        },
    ],
)
def test_c1_detail_rejects_malformed_managed_host_geometry(geometry):
    entity_type = "LWPOLYLINE" if "points" in geometry else "CIRCLE"
    capability = (
        "entity.geometry.polyline/1"
        if entity_type == "LWPOLYLINE"
        else "entity.geometry.circle/1"
    )
    entity = {
        "entity_id": "1A",
        "entity_type": entity_type,
        "layer": "0",
        "space": "model",
        "bounds": None,
        "geometry": geometry,
        "geometry_status": "exact",
        "geometry_reason": None,
        "geometry_truncated": False,
        "fingerprint": f"sha256:{'b' * 64}",
        "source_runtime": "managed_dotnet",
        "source_capabilities": [capability],
    }

    assert not DurableJobService._valid_c1_detail_entity(entity)


def test_c1_detail_rejects_spoofed_managed_geometry_provenance():
    entity = {
        "entity_id": "1A",
        "entity_type": "CIRCLE",
        "layer": "0",
        "space": "model",
        "bounds": None,
        "geometry": {
            "center": [0.0, 0.0],
            "radius": 1.0,
            "elevation": 0.0,
            "normal": [0.0, 0.0, 1.0],
        },
        "geometry_status": "exact",
        "geometry_reason": None,
        "geometry_truncated": False,
        "fingerprint": f"sha256:{'b' * 64}",
        "source_runtime": "managed_dotnet",
        "source_capabilities": ["entity.geometry.arc/1"],
    }

    assert not DurableJobService._valid_c1_detail_entity(entity)


def test_new_gateway_downgrades_legacy_agent_detail_geometry():
    entity = {
        "entity_id": "1A",
        "entity_type": "CIRCLE",
        "layer": "0",
        "space": "model",
        "bounds": None,
        "geometry": {
            "center": [0.0, 0.0],
            "radius": 1.0,
            "elevation": 0.0,
            "normal": [0.0, 0.0, 1.0],
        },
        "geometry_truncated": False,
        "fingerprint": f"sha256:{'b' * 64}",
    }
    snapshot = {"observation_level": "detail", "entities": [entity]}

    assert DurableJobService._valid_c1_detail_entity(entity)
    normalized = DurableJobService._normalize_c1_snapshot(snapshot)
    assert normalized["entities"][0] == {
        **entity,
        "geometry_status": "bounded_projection",
        "geometry_reason": "legacy_agent_provenance_unavailable",
        "source_runtime": "managed_dotnet_legacy",
        "source_capabilities": [],
    }
    assert "geometry_status" not in snapshot["entities"][0]
    node = project_entity(normalized["entities"][0])
    assert node.geometry_status == "bounded_projection"
    assert node.source_runtime == "managed_dotnet_legacy"
    assert node.source_capabilities == ()


def test_new_gateway_leaves_legacy_summary_snapshot_unchanged():
    snapshot = {
        "observation_level": "summary",
        "entities": [{"entity_id": "1A", "entity_type": "CIRCLE"}],
    }

    assert DurableJobService._normalize_c1_snapshot(snapshot) is snapshot


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
    managed_detail = copy.deepcopy(managed_snapshot)
    managed_detail["observation_level"] = "detail"
    managed_detail["document_revision"] = "7348076429262433"
    managed_detail["drawing"].update(
        {
            "document_id": "doc-live-1",
            "database_fingerprint": "{F1D66D07-A4F1-124E-A004-B5D05E6C6541}",
        }
    )
    managed_detail["entity_summary"] = {
        "entity_count": 1,
        "detail_available": True,
        "truncated": False,
    }
    managed_detail["entities"] = [
        {
            "entity_id": "1A",
            "entity_type": "LINE",
            "layer": "0",
            "space": "model",
            "bounds": {"min": [0, 0, 0], "max": [1, 1, 0]},
            "geometry": {"start": [0, 0], "end": [1, 1]},
            "geometry_status": "exact",
            "geometry_reason": None,
            "geometry_truncated": False,
            "fingerprint": f"sha256:{'b' * 64}",
            "source_runtime": "managed_dotnet",
            "source_capabilities": ["entity.geometry.line/1"],
        }
    ]
    managed_detail["revision_evidence"] = {
        "revision_schema": "cad.revision/1",
        "revision_strength": "database_object_fingerprint",
        "commit_safe": True,
    }
    managed_detail_result = copy.deepcopy(managed_result)
    managed_detail_result["snapshot"] = managed_detail
    assert (
        service.job_service._validate_c1_observation(
            managed_detail_result,
            managed_detail,
        )
        is None
    )
    legacy_detail = copy.deepcopy(managed_detail)
    for key in (
        "geometry_status",
        "geometry_reason",
        "source_runtime",
        "source_capabilities",
    ):
        legacy_detail["entities"][0].pop(key)
    legacy_result = copy.deepcopy(managed_result)
    legacy_result["snapshot"] = legacy_detail
    assert (
        service.job_service._validate_c1_observation(
            legacy_result,
            legacy_detail,
        )
        is None
    )
    legacy_node = service.job_service._normalize_c1_snapshot(
        legacy_detail
    )["entities"][0]
    assert legacy_node["geometry_status"] == "bounded_projection"
    assert legacy_node["source_capabilities"] == []
    assert RevisionEvidence.model_validate(
        managed_detail["revision_evidence"]
    ).commit_safe

    compatibility_detail = copy.deepcopy(managed_detail_result)
    for key in ("runtime", "degraded", "degradation_reason"):
        compatibility_detail["execution_evidence"].pop(key)
    assert (
        service.job_service._validate_c1_observation(
            compatibility_detail,
            managed_detail,
        )
        == "backend_error"
    )

    truncated_detail = copy.deepcopy(managed_detail)
    truncated_detail["entity_summary"]["truncated"] = True
    truncated_detail["revision_evidence"]["commit_safe"] = False
    truncated_result = copy.deepcopy(managed_detail_result)
    truncated_result["snapshot"] = truncated_detail
    assert (
        service.job_service._validate_c1_observation(
            truncated_result,
            truncated_detail,
        )
        is None
    )
    truncated_detail["revision_evidence"]["commit_safe"] = True
    assert (
        service.job_service._validate_c1_observation(
            truncated_result,
            truncated_detail,
        )
        == "backend_error"
    )

    mismatched_count = copy.deepcopy(managed_detail)
    mismatched_count["entity_summary"]["entity_count"] = 2
    mismatched_result = copy.deepcopy(managed_detail_result)
    mismatched_result["snapshot"] = mismatched_count
    assert (
        service.job_service._validate_c1_observation(
            mismatched_result,
            mismatched_count,
        )
        == "backend_error"
    )
    with pytest.raises(GatewayError) as captured:
        await service.query(CadQueryInput(snapshot_id="snapshot-c1"), principal, "corr-query")
    assert captured.value.code == "capability_missing"
    await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capabilities", "expected_contract"),
    [
        (("observe",), None),
        (
            ("observe", "cad.observe.detail-provenance/1"),
            "cad.observe-detail/2",
        ),
    ],
)
async def test_gateway_negotiates_detail_snapshot_contract(
    tmp_path,
    capabilities,
    expected_contract,
):
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
        request_wait_timeout_seconds=2,
    )
    await service.initialize()
    socket = Socket()
    connection = AgentConnection(
        device_id="device-lab",
        session_id="session-mixed-version",
        websocket=socket,
        protocol_version="cad.agent/1",
        capabilities=capabilities,
        capability_hash=canonical_capability_hash(capabilities),
        agent_version="0.1.0",
        runtime_state="online_idle",
        document_name="drawing33.dwg",
        packages=(PACKAGE,),
        package_manifest_hash=canonical_package_manifest_hash([PACKAGE]),
    )
    await registry.add(connection)
    await service.on_agent_connected(connection)
    task = asyncio.create_task(
        service.observe(
            CadObserveInputDurable(
                device_id="device-lab",
                observation_level="detail",
                idempotency_key="mixed-version-detail",
            ),
            Principal(
                subject=cfg.fixture_owner_subject,
                scopes=("autocad.read",),
            ),
            "corr-mixed-version",
        )
    )
    for _ in range(50):
        if socket.messages:
            break
        await asyncio.sleep(0.01)

    assert socket.messages[-1].detail_snapshot_contract == expected_contract
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
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
