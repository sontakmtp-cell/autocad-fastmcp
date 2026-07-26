from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest
from autocad_contracts import canonical_capability_hash
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from autocad_gateway.app import GatewayConfig
from autocad_gateway.contracts import (
    CadListDevicesInput,
    CadObserveInputDurable,
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


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def public_key(private_key: Ed25519PrivateKey) -> str:
    return b64(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )


async def pair(
    identity: Phase5IdentityService,
    private_key: Ed25519PrivateKey,
    *,
    device_id: str,
    issuer: str,
    subject: str,
) -> dict:
    started = await identity.start_pairing(
        device_id=device_id,
        display_name=device_id,
        public_key=public_key(private_key),
    )
    await identity.approve_pairing(
        issuer=issuer, subject=subject, user_code=started["user_code"]
    )
    signature = private_key.sign(
        f"cad.pair/1:{started['pairing_id']}:{started['challenge']}".encode()
    )
    return await identity.complete_pairing(
        pairing_id=started["pairing_id"],
        challenge=started["challenge"],
        signature=b64(signature),
    )


@pytest.fixture
async def phase5(tmp_path):
    database = SqliteDatabase(tmp_path / "phase5.sqlite3")
    await database.open()
    registry = ConnectionRegistry()
    identity = Phase5IdentityService(database, registry)
    try:
        yield database, registry, identity
    finally:
        await database.close()


def test_phase5_identity_rejects_insecure_oauth_urls(tmp_path):
    config = GatewayConfig(
        profile="phase5_identity",
        db_path=str(tmp_path / "phase5.sqlite3"),
        oauth_issuer="https://issuer.example/",
        oauth_audience="https://gateway.example",
        oauth_jwks_uri="http://issuer.example/.well-known/jwks.json",
        public_origin="https://gateway.example",
    )
    with pytest.raises(ValueError, match="canonical HTTPS"):
        config.validate()


@pytest.mark.asyncio
async def test_two_users_only_list_and_resolve_their_own_devices(phase5):
    database, registry, identity = phase5
    await pair(
        identity,
        Ed25519PrivateKey.generate(),
        device_id="device-a",
        issuer="https://issuer.example/",
        subject="user-a",
    )
    await pair(
        identity,
        Ed25519PrivateKey.generate(),
        device_id="device-b",
        issuer="https://issuer.example/",
        subject="user-b",
    )
    services = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase5_identity",
        agent_authenticator=object(),
    )
    user_a = Principal(
        subject=owner_key("https://issuer.example/", "user-a"),
        scopes=("autocad.read",),
    )
    listed = await services.list_devices(CadListDevicesInput(), user_a, "correlation")
    assert [device.device_id for device in listed.devices] == ["device-a"]
    with pytest.raises(GatewayError) as denied:
        await services._require_device("device-b", user_a)
    assert denied.value.code == "not_found"


@pytest.mark.asyncio
async def test_phase5_observe_pins_the_target_devices_advertised_package(phase5):
    database, registry, identity = phase5
    await pair(
        identity,
        Ed25519PrivateKey.generate(),
        device_id="device-a",
        issuer="https://issuer.example/",
        subject="user-a",
    )
    package = {
        "package_id": "autocad.lisp.drawing_info",
        "version": "3.3-c1",
        "sha256": "a" * 64,
    }
    services = DurableGatewayServices(
        database,
        registry,
        device_tokens={},
        profile="phase5_identity",
        agent_authenticator=object(),
    )
    await services.repository.activate_session(
        device_id="device-a",
        session_id="session-a",
        protocol_version="cad.agent/2",
        capabilities=["observe"],
        capability_hash=canonical_capability_hash(["observe"]),
        packages=[package],
    )
    services.job_service.create_and_observe = AsyncMock(
        return_value={
            "job_id": "job-a",
            "state": "failed",
            "error_code": "backend_error",
        }
    )
    principal = Principal(
        subject=owner_key("https://issuer.example/", "user-a"),
        scopes=("autocad.read",),
    )

    with pytest.raises(GatewayError) as captured:
        await services.observe(
            CadObserveInputDurable(device_id="device-a"),
            principal,
            "correlation",
        )

    assert captured.value.code == "backend_error"
    assert services.job_service.create_and_observe.await_args.kwargs["payload"] == {
        "observation_level": "summary",
        "include_preview_image": False,
        "package": package,
    }


@pytest.mark.asyncio
async def test_pairing_proof_key_swap_and_pairing_replay_are_rejected(phase5):
    _, _, identity = phase5
    key = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    started = await identity.start_pairing(
        device_id="device-a", display_name="A", public_key=public_key(key)
    )
    user_id = owner_key("https://issuer.example/", "user-a")
    preview = await identity.portal_pairing(
        owner_user_id=user_id, reference=started["user_code"]
    )
    assert preview["status"] == "pending"
    assert preview["device_name"] == "A"
    assert started["confirmation_url"].endswith(started["user_code"])
    await identity.confirm_pairing(
        issuer="https://issuer.example/",
        subject="user-a",
        reference=started["user_code"],
    )
    assert (
        await identity.pairing_status(
            pairing_id=started["pairing_id"],
            polling_secret=started["polling_secret"],
        )
    ) == {"status": "approved"}
    with pytest.raises(IdentityError, match="not_found"):
        await identity.pairing_status(
            pairing_id=started["pairing_id"],
            polling_secret="stolen-pairing-id-is-not-enough",
        )
    forged = attacker.sign(
        f"cad.pair/1:{started['pairing_id']}:{started['challenge']}".encode()
    )
    with pytest.raises(IdentityError, match="proof_invalid"):
        await identity.complete_pairing(
            pairing_id=started["pairing_id"],
            challenge=started["challenge"],
            signature=b64(forged),
        )
    valid = key.sign(
        f"cad.pair/1:{started['pairing_id']}:{started['challenge']}".encode()
    )
    await identity.complete_pairing(
        pairing_id=started["pairing_id"],
        challenge=started["challenge"],
        signature=b64(valid),
    )
    with pytest.raises(IdentityError, match="not_found"):
        await identity.complete_pairing(
            pairing_id=started["pairing_id"],
            challenge=started["challenge"],
            signature=b64(valid),
        )
    assert [device["id"] for device in await identity.portal_devices(user_id)] == [
        "device-a"
    ]
    with pytest.raises(IdentityError, match="not_found"):
        await identity.portal_device(
            owner_user_id=owner_key("https://issuer.example/", "user-b"),
            device_id="device-a",
        )


@pytest.mark.asyncio
async def test_pairing_attempts_and_outstanding_sessions_are_bounded(phase5):
    _, _, identity = phase5
    key = Ed25519PrivateKey.generate()
    started = await identity.start_pairing(
        device_id="device-a", display_name="A", public_key=public_key(key)
    )
    with pytest.raises(IdentityError, match="rate_limited"):
        await identity.start_pairing(
            device_id="device-a", display_name="A", public_key=public_key(key)
        )
    await identity.approve_pairing(
        issuer="https://issuer.example/",
        subject="user-a",
        user_code=started["user_code"],
    )
    for _ in range(5):
        with pytest.raises(IdentityError, match="proof_invalid"):
            await identity.complete_pairing(
                pairing_id=started["pairing_id"],
                challenge=started["challenge"],
                signature="invalid",
            )
    with pytest.raises(IdentityError, match="not_found"):
        await identity.complete_pairing(
            pairing_id=started["pairing_id"],
            challenge=started["challenge"],
            signature=b64(
                key.sign(
                    f"cad.pair/1:{started['pairing_id']}:{started['challenge']}".encode()
                )
            ),
        )


@pytest.mark.asyncio
async def test_pairing_can_be_denied_only_once(phase5):
    _, _, identity = phase5
    started = await identity.start_pairing(
        device_id="device-a",
        display_name="A",
        public_key=public_key(Ed25519PrivateKey.generate()),
    )
    user_id = owner_key("https://issuer.example/", "user-a")
    assert await identity.deny_pairing(
        issuer="https://issuer.example/",
        subject="user-a",
        reference=started["user_code"],
    ) == {"status": "denied"}
    assert (
        await identity.portal_pairing(
            owner_user_id=user_id, reference=started["pairing_id"]
        )
    )["status"] == "denied"
    with pytest.raises(IdentityError, match="not_found"):
        await identity.confirm_pairing(
            issuer="https://issuer.example/",
            subject="user-a",
            reference=started["user_code"],
        )


@pytest.mark.asyncio
async def test_challenge_and_wss_tokens_are_one_time(phase5):
    _, _, identity = phase5
    key = Ed25519PrivateKey.generate()
    await pair(
        identity,
        key,
        device_id="device-a",
        issuer="https://issuer.example/",
        subject="user-a",
    )
    challenge = await identity.create_challenge("device-a")
    replacement = await identity.create_challenge("device-a")
    with pytest.raises(IdentityError, match="not_found"):
        await identity.exchange_challenge(
            device_id="device-a",
            challenge_id=challenge["challenge_id"],
            challenge=challenge["challenge"],
            signature=b64(
                key.sign(
                    (
                        f"cad.challenge/1:device-a:{challenge['challenge_id']}:"
                        f"{challenge['challenge']}"
                    ).encode()
                )
            ),
        )
    challenge = replacement
    signature = key.sign(
        (
            f"cad.challenge/1:device-a:{challenge['challenge_id']}:"
            f"{challenge['challenge']}"
        ).encode()
    )
    token = await identity.exchange_challenge(
        device_id="device-a",
        challenge_id=challenge["challenge_id"],
        challenge=challenge["challenge"],
        signature=b64(signature),
    )
    with pytest.raises(IdentityError, match="not_found"):
        await identity.exchange_challenge(
            device_id="device-a",
            challenge_id=challenge["challenge_id"],
            challenge=challenge["challenge"],
            signature=b64(signature),
        )
    assert await identity.consume_access_token(token["access_token"]) == "device-a"
    with pytest.raises(IdentityError, match="auth_failed"):
        await identity.consume_access_token(token["access_token"])


class Socket:
    def __init__(self) -> None:
        self.closed = None

    async def close(self, *, code, reason):
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_revoke_closes_socket_and_invalidates_admission(phase5):
    _, registry, identity = phase5
    key = Ed25519PrivateKey.generate()
    token = await pair(
        identity,
        key,
        device_id="device-a",
        issuer="https://issuer.example/",
        subject="user-a",
    )
    socket = Socket()
    await registry.add(
        AgentConnection(
            device_id="device-a",
            session_id="session-a",
            websocket=socket,
            protocol_version="cad.agent/2",
        )
    )
    await identity.revoke(
        owner_user_id=owner_key("https://issuer.example/", "user-a"),
        device_id="device-a",
    )
    assert socket.closed == (4403, "device revoked")
    assert await registry.get("device-a") is None
    with pytest.raises(IdentityError, match="auth_failed"):
        await identity.consume_access_token(token["access_token"])
    with pytest.raises(IdentityError, match="credential_revoked"):
        await identity.create_challenge("device-a")
    owner = owner_key("https://issuer.example/", "user-a")
    assert await identity.portal_devices(owner) == []
    with pytest.raises(IdentityError, match="not_found"):
        await identity.portal_device(owner_user_id=owner, device_id="device-a")


@pytest.mark.asyncio
async def test_other_owner_cannot_revoke_device(phase5):
    _, _, identity = phase5
    await pair(
        identity,
        Ed25519PrivateKey.generate(),
        device_id="device-a",
        issuer="https://issuer.example/",
        subject="user-a",
    )
    with pytest.raises(IdentityError, match="not_found"):
        await identity.revoke(
            owner_user_id=owner_key("https://issuer.example/", "user-b"),
            device_id="device-a",
        )
