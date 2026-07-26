from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autocad_gateway.contracts import (
    CadGetJobInput,
    CadListDevicesInput,
    CadObserveInput,
    CadQueryInput,
    Principal,
)
from autocad_gateway.durable_services import DurableGatewayServices
from autocad_gateway.identity import IdentityError, Phase5IdentityService, owner_key
from autocad_gateway.infrastructure.agent_transport.connection_registry import (
    AgentConnection,
    ConnectionRegistry,
)
from autocad_gateway.infrastructure.sqlite.database import SqliteDatabase
from autocad_gateway.services import GatewayError


ISSUER = "https://issuer.example/"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _public_key(private_key: Ed25519PrivateKey) -> str:
    return _b64(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )


async def _pair(
    identity: Phase5IdentityService,
    private_key: Ed25519PrivateKey,
    *,
    device_id: str,
    subject: str,
) -> dict:
    started = await identity.start_pairing(
        device_id=device_id,
        display_name=device_id,
        public_key=_public_key(private_key),
    )
    await identity.approve_pairing(
        issuer=ISSUER,
        subject=subject,
        user_code=started["user_code"],
    )
    signature = private_key.sign(
        f"cad.pair/1:{started['pairing_id']}:{started['challenge']}".encode()
    )
    return await identity.complete_pairing(
        pairing_id=started["pairing_id"],
        challenge=started["challenge"],
        signature=_b64(signature),
    )


class _Socket:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict] = []

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.mark.asyncio
async def test_two_users_two_devices_are_isolated_and_revoke_only_target_device(
    tmp_path,
):
    database = SqliteDatabase(tmp_path / "phase56-isolation.sqlite3")
    await database.open()
    registry = ConnectionRegistry()
    identity = Phase5IdentityService(database, registry)
    services = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase5_identity",
        agent_authenticator=object(),
    )
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()
    owner_a = owner_key(ISSUER, "user-a")
    owner_b = owner_key(ISSUER, "user-b")
    principal_a = Principal(subject=owner_a, scopes=("autocad.read",))
    principal_b = Principal(subject=owner_b, scopes=("autocad.read",))

    try:
        admission_a = await _pair(
            identity,
            key_a,
            device_id="device-a",
            subject="user-a",
        )
        admission_b = await _pair(
            identity,
            key_b,
            device_id="device-b",
            subject="user-b",
        )

        socket_a = _Socket()
        socket_b = _Socket()
        await registry.add(
            AgentConnection(
                device_id="device-a",
                session_id="session-a",
                websocket=socket_a,
                protocol_version="cad.agent/2",
            )
        )
        await registry.add(
            AgentConnection(
                device_id="device-b",
                session_id="session-b",
                websocket=socket_b,
                protocol_version="cad.agent/2",
            )
        )

        devices_a = await services.list_devices(
            CadListDevicesInput(), principal_a, "list-a"
        )
        devices_b = await services.list_devices(
            CadListDevicesInput(), principal_b, "list-b"
        )
        assert [device.device_id for device in devices_a.devices] == ["device-a"]
        assert [device.device_id for device in devices_b.devices] == ["device-b"]

        # Authorization fails before a cross-owner request can create or dispatch work.
        with pytest.raises(GatewayError) as cross_device:
            await services.observe(
                CadObserveInput(device_id="device-a"),
                principal_b,
                "cross-owner-observe",
            )
        assert cross_device.value.code == "not_found"
        assert socket_a.sent == []
        assert socket_b.sent == []

        job_a = await services.repository.create_job(
            owner_subject=owner_a,
            device_id="device-a",
            kind="observe",
            effect_class="read",
            payload={
                "observation_level": "summary",
                "include_preview_image": False,
            },
            idempotency_key="owned-a",
            deadline_at=None,
        )
        await services.repository.claim_job(job_a["job_id"])
        await services.repository.transition_job(job_a["job_id"], "acknowledged")
        running_a = await services.repository.transition_job(job_a["job_id"], "running")
        snapshot_a = {
            "snapshot_id": "snapshot-a",
            "document_revision": "revision-a",
            "observation_level": "summary",
            "drawing": {"name": "drawing33.dwg"},
            "entity_summary": {"entity_count": 0},
            "entities": [],
            "revision_evidence": {
                "revision_schema": "cad.revision/1",
                "revision_strength": "event_and_database",
                "commit_safe": False,
            },
        }
        await services.repository.finalize_job_result(
            job_id=job_a["job_id"],
            device_id="device-a",
            command_id=job_a["command_id"],
            payload_hash=job_a["payload_hash"],
            target="succeeded",
            result={"snapshot": snapshot_a},
            snapshot=snapshot_a,
            expected_version=running_a["state_version"],
        )

        owned_job = await services.get_job(
            CadGetJobInput(job_id=job_a["job_id"]),
            principal_a,
            "owned-job",
        )
        owned_snapshot = await services.query(
            CadQueryInput(snapshot_id="snapshot-a"),
            principal_a,
            "owned-snapshot",
        )
        assert owned_job.job_id == job_a["job_id"]
        assert owned_snapshot.snapshot_id == "snapshot-a"

        with pytest.raises(GatewayError) as cross_job:
            await services.get_job(
                CadGetJobInput(job_id=job_a["job_id"]),
                principal_b,
                "cross-owner-job",
            )
        assert cross_job.value.code == "not_found"
        with pytest.raises(GatewayError) as cross_snapshot:
            await services.query(
                CadQueryInput(snapshot_id="snapshot-a"),
                principal_b,
                "cross-owner-snapshot",
            )
        assert cross_snapshot.value.code == "not_found"
        with pytest.raises(GatewayError) as cross_artifact:
            await services.read_artifact("artifact-a", principal_b)
        assert cross_artifact.value.code == "not_found"
        assert socket_a.sent == []
        assert socket_b.sent == []

        await identity.revoke(owner_user_id=owner_a, device_id="device-a")
        assert socket_a.closed == (4403, "device revoked")
        assert await registry.get("device-a") is None
        assert socket_b.closed is None
        assert await registry.get("device-b") is not None

        with pytest.raises(IdentityError, match="auth_failed"):
            await identity.consume_access_token(admission_a["access_token"])
        with pytest.raises(IdentityError, match="credential_revoked"):
            await identity.create_challenge("device-a")

        assert (
            await identity.consume_access_token(admission_b["access_token"])
            == "device-b"
        )
        challenge_b = await identity.create_challenge("device-b")
        signature_b = key_b.sign(
            (
                f"cad.challenge/1:device-b:{challenge_b['challenge_id']}:"
                f"{challenge_b['challenge']}"
            ).encode()
        )
        replacement_b = await identity.exchange_challenge(
            device_id="device-b",
            challenge_id=challenge_b["challenge_id"],
            challenge=challenge_b["challenge"],
            signature=_b64(signature_b),
        )
        assert replacement_b["access_token"]
    finally:
        await database.close()
