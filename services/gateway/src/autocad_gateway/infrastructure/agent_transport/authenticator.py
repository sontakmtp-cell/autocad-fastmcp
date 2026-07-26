"""Fixture-only Agent authentication for the Phase 3 POC."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from autocad_contracts import PHASE5_PROTOCOL_VERSION

from ...identity import IdentityError, Phase5IdentityService


class FixtureAuthError(ValueError):
    pass


class FixtureDeviceAuthenticator:
    """Maps an opaque test token to one seeded device; never used by local/OAuth profiles."""

    def __init__(self, tokens: dict[str, str]) -> None:
        if not tokens or any(not device or not token for device, token in tokens.items()):
            raise ValueError("fixture authenticator requires non-empty device tokens")
        self._tokens = dict(tokens)

    def authenticate(self, token: str) -> str:
        for device_id, expected in self._tokens.items():
            if token == expected:
                return device_id
        raise FixtureAuthError("fixture authentication failed")

    def verify_hello(self, hello: Any, token: str) -> bool:
        return hmac.compare_digest(hello.fixture_proof, token)

    @property
    def device_ids(self) -> tuple[str, ...]:
        return tuple(self._tokens)


class LabDeviceAuthenticator(FixtureDeviceAuthenticator):
    """One-device C1 authenticator with a bound hello proof.

    This remains a lab credential, not production pairing.  The proof avoids
    repeating the raw credential in the protocol payload.
    """

    def verify_hello(self, hello: Any, token: str) -> bool:
        proof = getattr(hello, "device_proof", None)
        if not isinstance(proof, str):
            return False
        expected = hmac.new(
            token.encode("utf-8"),
            f"{hello.device_id}:{hello.message_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(proof, expected)


class PairedDeviceAuthenticator:
    """Database-backed cad.agent/2 admission with one-time bearer tokens."""

    protocol_version = PHASE5_PROTOCOL_VERSION

    def __init__(self, identity: Phase5IdentityService) -> None:
        self.identity = identity

    async def authenticate(self, token: str) -> str:
        try:
            return await self.identity.consume_access_token(token)
        except IdentityError as error:
            raise FixtureAuthError("paired device authentication failed") from error

    async def verify_hello(self, hello: Any, token: str) -> bool:
        return await self.identity.verify_hello(hello.device_id, hello, token)

    async def is_active(self, device_id: str) -> bool:
        return await self.identity.is_active_device(device_id)
