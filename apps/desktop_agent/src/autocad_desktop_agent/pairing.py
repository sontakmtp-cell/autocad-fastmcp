"""Production device-key storage and Phase 5 pairing/session client."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class BlobProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class WindowsDpapiProtector:
    """Protect device material for the current Windows user only."""

    _description = "AutoCAD MCP paired device key"

    def protect(self, value: bytes) -> bytes:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI device storage requires Windows")
        import win32crypt

        return win32crypt.CryptProtectData(
            value,
            self._description,
            None,
            None,
            None,
            0,
        )

    def unprotect(self, value: bytes) -> bytes:
        if sys.platform != "win32":
            raise RuntimeError("DPAPI device storage requires Windows")
        import win32crypt

        return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1]


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    public_key: str
    key_fingerprint: str


class DeviceIdentityStore:
    """Persist a non-secret device ID and a DPAPI-protected Ed25519 key."""

    def __init__(
        self,
        root: str | Path,
        *,
        protector: BlobProtector | None = None,
    ) -> None:
        self.root = Path(root)
        self._protector = protector or WindowsDpapiProtector()
        self._metadata_path = self.root / "device.json"
        self._key_path = self.root / "device.key.dpapi"
        self._paired_path = self.root / "paired.json"

    def ensure(self) -> DeviceIdentity:
        if self._metadata_path.is_file() or self._key_path.is_file():
            return self.load_identity()
        private_key = Ed25519PrivateKey.generate()
        device_id = f"device-{uuid.uuid4()}"
        public_key = _b64url(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        fingerprint = hashlib.sha256(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).hexdigest()
        protected = self._protector.protect(
            private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_private(protected)
        self._write_metadata(
            {
                "schema": "cad.device.identity/1",
                "device_id": device_id,
                "public_key": public_key,
                "key_fingerprint": fingerprint,
            }
        )
        return DeviceIdentity(device_id, public_key, fingerprint)

    def load_identity(self) -> DeviceIdentity:
        try:
            metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("paired device identity is unavailable") from error
        if metadata.get("schema") != "cad.device.identity/1":
            raise RuntimeError("paired device identity is invalid")
        identity = DeviceIdentity(
            device_id=str(metadata.get("device_id", "")),
            public_key=str(metadata.get("public_key", "")),
            key_fingerprint=str(metadata.get("key_fingerprint", "")),
        )
        if (
            not identity.device_id.startswith("device-")
            or len(identity.public_key) != 43
            or len(identity.key_fingerprint) != 64
        ):
            raise RuntimeError("paired device identity is invalid")
        private_key = self._private_key()
        actual_public = _b64url(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        if actual_public != identity.public_key:
            raise RuntimeError("paired device key does not match metadata")
        return identity

    def sign(self, message: str) -> str:
        if not isinstance(message, str) or not message:
            raise ValueError("signed message is required")
        return _b64url(self._private_key().sign(message.encode("utf-8")))

    def mark_paired(self) -> None:
        self._write_json(
            self._paired_path,
            {"schema": "cad.device.pairing-state/1", "paired": True},
        )

    def is_paired(self) -> bool:
        try:
            value = json.loads(self._paired_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return value == {"schema": "cad.device.pairing-state/1", "paired": True}

    def remove_after_revoke(self) -> None:
        """Delete local identity only after the server confirmed revocation."""

        for path in (self._key_path, self._metadata_path, self._paired_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _private_key(self) -> Ed25519PrivateKey:
        try:
            protected = self._key_path.read_bytes()
            raw = self._protector.unprotect(protected)
            return Ed25519PrivateKey.from_private_bytes(raw)
        except (OSError, ValueError) as error:
            raise RuntimeError("paired device key is unavailable") from error

    def _write_private(self, value: bytes) -> None:
        temporary = self._key_path.with_suffix(".tmp")
        temporary.write_bytes(value)
        os.replace(temporary, self._key_path)

    def _write_metadata(self, value: dict[str, Any]) -> None:
        self._write_json(self._metadata_path, value)

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)


class PairingApiClient:
    """Bounded HTTP client for enrollment and one-time WSS token exchange."""

    def __init__(
        self,
        base_url: str,
        identity_store: DeviceIdentityStore,
        *,
        portal_url: str | None = None,
        timeout_seconds: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("pairing API requires HTTPS or loopback HTTP")
        self.portal_url = (portal_url or self.base_url).rstrip("/")
        portal = urlsplit(self.portal_url)
        if portal.scheme not in {"https", "http"} or not portal.netloc:
            raise ValueError("pairing Portal requires an absolute URL")
        if portal.scheme == "http" and portal.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("pairing Portal requires HTTPS or loopback HTTP")
        self.identity_store = identity_store
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def start(self, display_name: str) -> dict[str, Any]:
        identity = self.identity_store.ensure()
        enrollment = await self._post(
            "/api/agent/v1/enrollments",
            {
                "device_id": identity.device_id,
                "display_name": display_name,
                "public_key": identity.public_key,
            },
        )
        confirmation = enrollment.get("confirmation_url")
        if not isinstance(confirmation, str) or not confirmation.startswith("/"):
            raise RuntimeError("Gateway returned an invalid confirmation URL")
        enrollment["confirmation_url"] = urljoin(
            f"{self.portal_url}/",
            confirmation.lstrip("/"),
        )
        return enrollment

    async def status(
        self,
        *,
        pairing_id: str,
        polling_secret: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/agent/v1/enrollments/{pairing_id}",
            headers={"X-Polling-Secret": polling_secret},
        )

    async def complete(
        self,
        *,
        pairing_id: str,
        challenge: str,
    ) -> dict[str, Any]:
        signature = self.identity_store.sign(
            f"cad.pair/1:{pairing_id}:{challenge}"
        )
        result = await self._post(
            f"/api/agent/v1/enrollments/{pairing_id}/complete",
            {
                "pairing_id": pairing_id,
                "challenge": challenge,
                "signature": signature,
            },
        )
        self.identity_store.mark_paired()
        return result

    async def session_token(self) -> str:
        identity = self.identity_store.load_identity()
        challenge = await self._post(
            "/api/agent/v1/session-challenges",
            {"device_id": identity.device_id},
        )
        challenge_id = str(challenge["challenge_id"])
        challenge_value = str(challenge["challenge"])
        signature = self.identity_store.sign(
            "cad.challenge/1:"
            f"{identity.device_id}:{challenge_id}:{challenge_value}"
        )
        token = await self._post(
            "/api/agent/v1/session-tokens",
            {
                "device_id": identity.device_id,
                "challenge_id": challenge_id,
                "challenge": challenge_value,
                "signature": signature,
            },
        )
        if token.get("token_type") != "Bearer" or not token.get("access_token"):
            raise RuntimeError("Gateway returned an invalid device session token")
        return str(token["access_token"])

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, body=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
            trust_env=False,
        ) as client:
            request_options: dict[str, Any] = {"headers": headers}
            if body is not None:
                request_options["json"] = body
            response = await client.request(method, path, **request_options)
        if response.status_code == 410:
            self.identity_store.remove_after_revoke()
            raise RuntimeError("credential_revoked")
        if response.status_code >= 400:
            raise RuntimeError("pairing_request_failed")
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("Gateway returned an invalid pairing response")
        return value


class PairedCredentialProvider:
    """Acquire one short-lived token and bind cad.agent/2 Hello to it."""

    protocol_version = "cad.agent/2"

    def __init__(self, api: PairingApiClient) -> None:
        self.api = api

    async def load(self) -> str:
        return await self.api.session_token()

    def hello_proof(self, message_id: str, token: str) -> str:
        identity = self.api.identity_store.load_identity()
        return self.api.identity_store.sign(
            f"cad.agent/2:{identity.device_id}:{message_id}:{token}"
        )
