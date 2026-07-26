from __future__ import annotations

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from autocad_desktop_agent.pairing import DeviceIdentityStore, PairingApiClient


class TestProtector:
    def protect(self, value: bytes) -> bytes:
        return b"protected:" + value

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(b"protected:"):
            raise ValueError("invalid protected value")
        return value.removeprefix(b"protected:")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_identity_is_stable_and_private_key_is_not_plaintext(tmp_path):
    store = DeviceIdentityStore(tmp_path, protector=TestProtector())

    first = store.ensure()
    second = store.ensure()

    assert first == second
    assert first.device_id.startswith("device-")
    assert len(first.public_key) == 43
    assert len(first.key_fingerprint) == 64
    protected = (tmp_path / "device.key.dpapi").read_bytes()
    assert b"protected:" in protected
    assert first.public_key.encode() not in protected


def test_metadata_key_substitution_is_rejected(tmp_path):
    store = DeviceIdentityStore(tmp_path, protector=TestProtector())
    store.ensure()
    metadata_path = tmp_path / "device.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["public_key"] = "A" * 43
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not match"):
        store.load_identity()


def test_signature_is_bound_to_stored_public_key(tmp_path):
    store = DeviceIdentityStore(tmp_path, protector=TestProtector())
    identity = store.ensure()
    message = "cad.challenge/1:device:test"

    signature = _decode(store.sign(message))

    Ed25519PublicKey.from_public_bytes(_decode(identity.public_key)).verify(
        signature,
        message.encode(),
    )


@pytest.mark.asyncio
async def test_pairing_and_session_exchange_sign_exact_challenges(tmp_path):
    store = DeviceIdentityStore(tmp_path, protector=TestProtector())
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/enrollments"):
            return httpx.Response(
                200,
                json={
                    "pairing_id": "pair-1",
                    "user_code": "ABCD2345",
                    "challenge": "pair-challenge",
                    "confirmation_url": "/pair?request=ABCD2345",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            )
        if request.method == "GET":
            assert request.headers["X-Polling-Secret"] == "polling-secret"
            return httpx.Response(
                200,
                json={"pairing_id": "pair-1", "status": "approved"},
            )
        if request.url.path.endswith("/complete"):
            return httpx.Response(200, json={"status": "paired"})
        if request.url.path.endswith("/session-challenges"):
            return httpx.Response(
                200,
                json={
                    "challenge_id": "challenge-1",
                    "challenge": "session-challenge",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            )
        return httpx.Response(
            200,
            json={"access_token": "one-time", "token_type": "Bearer", "expires_in": 60},
        )

    client = PairingApiClient(
        "https://gateway.example",
        store,
        transport=httpx.MockTransport(handler),
    )
    started = await client.start("VM phase4lab")
    assert started["confirmation_url"] == (
        "https://gateway.example/pair?request=ABCD2345"
    )
    status = await client.status(
        pairing_id=started["pairing_id"],
        polling_secret="polling-secret",
    )
    await client.complete(
        pairing_id=started["pairing_id"],
        challenge=started["challenge"],
    )
    token = await client.session_token()

    assert token == "one-time"
    assert store.is_paired() is True
    assert status["pairing_id"] == "pair-1"
    assert [path for path, _ in seen] == [
        "/api/agent/v1/enrollments",
        "/api/agent/v1/enrollments/pair-1",
        "/api/agent/v1/enrollments/pair-1/complete",
        "/api/agent/v1/session-challenges",
        "/api/agent/v1/session-tokens",
    ]
    identity = store.load_identity()
    public_key = Ed25519PublicKey.from_public_bytes(_decode(identity.public_key))
    public_key.verify(
        _decode(seen[2][1]["signature"]),
        b"cad.pair/1:pair-1:pair-challenge",
    )
    public_key.verify(
        _decode(seen[4][1]["signature"]),
        (
            f"cad.challenge/1:{identity.device_id}:"
            "challenge-1:session-challenge"
        ).encode(),
    )


def test_pairing_client_rejects_public_plain_http(tmp_path):
    store = DeviceIdentityStore(tmp_path, protector=TestProtector())
    with pytest.raises(ValueError):
        PairingApiClient("http://example.com", store)


def test_confirmed_revoke_removes_only_local_device_identity(tmp_path):
    store = DeviceIdentityStore(tmp_path, protector=TestProtector())
    store.ensure()
    store.mark_paired()

    store.remove_after_revoke()

    assert store.is_paired() is False
    assert not (tmp_path / "device.json").exists()
    assert not (tmp_path / "device.key.dpapi").exists()
