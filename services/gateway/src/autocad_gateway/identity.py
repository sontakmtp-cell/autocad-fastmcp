"""Phase 5 multi-user identity, pairing, proof and revocation."""

from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .infrastructure.sqlite.database import SqliteDatabase, new_id, utc_now


class IdentityError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def owner_key(issuer: str, subject: str) -> str:
    """Stable, non-email owner identifier derived from the OAuth authority pair."""

    if not issuer or not subject:
        raise IdentityError("invalid_token")
    digest = hashlib.sha256(f"{issuer}\0{subject}".encode("utf-8")).hexdigest()
    return f"user-{digest}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64decode(value: str, expected_bytes: int) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as error:
        raise IdentityError("invalid_request") from error
    if len(raw) != expected_bytes:
        raise IdentityError("invalid_request")
    return raw


def _verify(public_key: str, signature: str, message: str) -> None:
    try:
        key = Ed25519PublicKey.from_public_bytes(_b64decode(public_key, 32))
        key.verify(_b64decode(signature, 64), message.encode("utf-8"))
    except (IdentityError, InvalidSignature, ValueError) as error:
        raise IdentityError("proof_invalid") from error


def _future(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class Phase5IdentityService:
    def __init__(
        self,
        database: SqliteDatabase,
        registry: Any,
        *,
        pairing_ttl_seconds: int = 600,
        challenge_ttl_seconds: int = 60,
        token_ttl_seconds: int = 60,
    ) -> None:
        self.database = database
        self.registry = registry
        self.pairing_ttl_seconds = pairing_ttl_seconds
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.token_ttl_seconds = token_ttl_seconds

    async def start_pairing(
        self, *, device_id: str, display_name: str, public_key: str
    ) -> dict[str, Any]:
        _b64decode(public_key, 32)
        pairing_id = new_id("pair")
        user_code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        challenge = secrets.token_urlsafe(32)
        polling_secret = secrets.token_urlsafe(32)
        now = utc_now()
        expires_at = _future(self.pairing_ttl_seconds)
        with self.database.transaction() as conn:
            conn.execute(
                "DELETE FROM pairing_sessions "
                "WHERE expires_at <= ? AND state IN ('pending', 'approved', 'denied', 'expired')",
                (now,),
            )
            active_count = conn.execute(
                "SELECT COUNT(*) FROM pairing_sessions "
                "WHERE state IN ('pending', 'approved') AND expires_at > ?",
                (now,),
            ).fetchone()[0]
            if int(active_count) >= 1000:
                raise IdentityError("rate_limited")
            pending = conn.execute(
                "SELECT 1 FROM pairing_sessions "
                "WHERE device_id = ? AND state IN ('pending', 'approved') "
                "AND expires_at > ?",
                (device_id, now),
            ).fetchone()
            if pending is not None:
                raise IdentityError("rate_limited")
            credential = conn.execute(
                "SELECT 1 FROM device_credentials WHERE device_id = ? AND revoked_at IS NULL",
                (device_id,),
            ).fetchone()
            if credential is not None:
                raise IdentityError("device_already_paired")
            conn.execute(
                """
                INSERT INTO pairing_sessions(
                    pairing_id, user_code_hash, device_id, display_name, public_key,
                    challenge_hash, polling_secret_hash, state, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    pairing_id,
                    _hash(user_code),
                    device_id,
                    display_name,
                    public_key,
                    _hash(challenge),
                    _hash(polling_secret),
                    expires_at,
                    now,
                ),
            )
        return {
            "pairing_id": pairing_id,
            "user_code": user_code,
            "challenge": challenge,
            "polling_secret": polling_secret,
            "confirmation_url": f"/pair?request={user_code}",
            "expires_at": expires_at,
        }

    async def pairing_status(self, *, pairing_id: str, polling_secret: str) -> dict[str, str]:
        now = utc_now()
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT state, polling_secret_hash, expires_at FROM pairing_sessions "
                "WHERE pairing_id = ?",
                (pairing_id,),
            ).fetchone()
        if row is None or not secrets.compare_digest(
            str(row["polling_secret_hash"]), _hash(polling_secret)
        ):
            raise IdentityError("not_found")
        state = str(row["state"])
        if row["expires_at"] <= now and state in {"pending", "approved"}:
            state = "expired"
        return {"status": state}

    async def approve_pairing(
        self, *, issuer: str, subject: str, user_code: str
    ) -> dict[str, str]:
        user_id = owner_key(issuer, subject)
        now = utc_now()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM pairing_sessions WHERE user_code_hash = ?",
                (_hash(user_code.upper()),),
            ).fetchone()
            if row is None or row["state"] != "pending" or row["expires_at"] <= now:
                raise IdentityError("not_found")
            conn.execute(
                """
                INSERT INTO users(user_id, issuer, subject, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(issuer, subject) DO UPDATE SET last_seen_at=excluded.last_seen_at
                """,
                (user_id, issuer, subject, now, now),
            )
            conn.execute(
                "UPDATE pairing_sessions SET owner_user_id = ?, state = 'approved' "
                "WHERE pairing_id = ? AND state = 'pending'",
                (user_id, row["pairing_id"]),
            )
            self._audit(conn, user_id, row["device_id"], "pairing_approved", "success")
        return {"status": "approved", "pairing_id": str(row["pairing_id"])}

    async def portal_pairing(
        self, *, owner_user_id: str, reference: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.read_connection() as conn:
            row = self._pairing_by_reference(conn, reference)
        if row is None:
            raise IdentityError("not_found")
        if row["owner_user_id"] is not None and row["owner_user_id"] != owner_user_id:
            raise IdentityError("not_found")
        state = str(row["state"])
        if row["expires_at"] <= now and state in {"pending", "approved"}:
            state = "expired"
        portal_state = {
            "pending": "pending",
            "approved": "confirmed",
            "completed": "confirmed",
            "denied": "denied",
            "expired": "expired",
        }[state]
        return {
            "id": str(row["pairing_id"]),
            "device_name": str(row["display_name"]),
            "requested_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]),
            "status": portal_state,
        }

    async def confirm_pairing(
        self, *, issuer: str, subject: str, reference: str
    ) -> dict[str, str]:
        user_id = owner_key(issuer, subject)
        now = utc_now()
        with self.database.transaction() as conn:
            row = self._pairing_by_reference(conn, reference)
            if row is None or row["state"] != "pending" or row["expires_at"] <= now:
                raise IdentityError("not_found")
            conn.execute(
                """
                INSERT INTO users(user_id, issuer, subject, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(issuer, subject) DO UPDATE SET last_seen_at=excluded.last_seen_at
                """,
                (user_id, issuer, subject, now, now),
            )
            conn.execute(
                "UPDATE pairing_sessions SET owner_user_id = ?, state = 'approved' "
                "WHERE pairing_id = ? AND state = 'pending'",
                (user_id, row["pairing_id"]),
            )
            self._audit(conn, user_id, row["device_id"], "pairing_approved", "success")
        return {"status": "confirmed"}

    async def deny_pairing(
        self, *, issuer: str, subject: str, reference: str
    ) -> dict[str, str]:
        owner_user_id = owner_key(issuer, subject)
        now = utc_now()
        with self.database.transaction() as conn:
            row = self._pairing_by_reference(conn, reference)
            if (
                row is None
                or row["state"] != "pending"
                or row["expires_at"] <= now
                or (
                    row["owner_user_id"] is not None
                    and row["owner_user_id"] != owner_user_id
                )
            ):
                raise IdentityError("not_found")
            conn.execute(
                """
                INSERT INTO users(user_id, issuer, subject, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(issuer, subject) DO UPDATE SET last_seen_at=excluded.last_seen_at
                """,
                (owner_user_id, issuer, subject, now, now),
            )
            conn.execute(
                "UPDATE pairing_sessions SET owner_user_id = ?, state = 'denied' "
                "WHERE pairing_id = ? AND state = 'pending'",
                (owner_user_id, row["pairing_id"]),
            )
            self._audit(conn, owner_user_id, row["device_id"], "pairing_denied", "success")
        return {"status": "denied"}

    async def portal_devices(self, owner_user_id: str) -> list[dict[str, Any]]:
        with self.database.read_connection() as conn:
            rows = conn.execute(
                "SELECT d.* FROM devices d "
                "JOIN device_credentials c ON c.device_id = d.device_id "
                "WHERE d.owner_subject = ? AND c.revoked_at IS NULL "
                "ORDER BY d.device_id",
                (owner_user_id,),
            ).fetchall()
        return [
            self._portal_device(row, is_default=index == 0)
            for index, row in enumerate(rows)
        ]

    async def portal_device(
        self, *, owner_user_id: str, device_id: str
    ) -> dict[str, Any]:
        devices = await self.portal_devices(owner_user_id)
        for device in devices:
            if device["id"] == device_id:
                return device
        raise IdentityError("not_found")

    async def complete_pairing(
        self, *, pairing_id: str, challenge: str, signature: str
    ) -> dict[str, Any]:
        now = utc_now()
        proof_invalid = False
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM pairing_sessions WHERE pairing_id = ?", (pairing_id,)
            ).fetchone()
            if (
                row is None
                or row["state"] != "approved"
                or int(row["attempts"]) >= 5
                or row["expires_at"] <= now
                or not secrets.compare_digest(str(row["challenge_hash"]), _hash(challenge))
            ):
                raise IdentityError("not_found")
            try:
                _verify(
                    str(row["public_key"]),
                    signature,
                    f"cad.pair/1:{pairing_id}:{challenge}",
                )
            except IdentityError:
                attempts = int(row["attempts"]) + 1
                conn.execute(
                    "UPDATE pairing_sessions SET attempts = ?, "
                    "state = CASE WHEN ? >= 5 THEN 'denied' ELSE state END "
                    "WHERE pairing_id = ?",
                    (attempts, attempts, pairing_id),
                )
                self._audit(
                    conn, row["owner_user_id"], row["device_id"], "pairing_completed", "denied"
                )
                proof_invalid = True
            if not proof_invalid:
                existing = conn.execute(
                    "SELECT owner_subject FROM devices WHERE device_id = ?",
                    (row["device_id"],),
                ).fetchone()
                if existing is not None:
                    raise IdentityError("device_already_paired")
                capabilities = '["observe","query"]'
                key_fingerprint = hashlib.sha256(
                    _b64decode(str(row["public_key"]), 32)
                ).hexdigest()
                conn.execute(
                    """
                    INSERT INTO devices(
                        device_id, owner_subject, display_name, status, capabilities_json,
                        fixture_auth_ref, created_at, updated_at, capability_hash
                    ) VALUES (?, ?, ?, 'offline', ?, ?, ?, ?, ?)
                    """,
                    (
                        row["device_id"],
                        row["owner_user_id"],
                        row["display_name"],
                        capabilities,
                        f"ed25519:{key_fingerprint}",
                        now,
                        now,
                        hashlib.sha256(capabilities.encode()).hexdigest(),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO device_credentials(
                        device_id, public_key, key_fingerprint, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (row["device_id"], row["public_key"], key_fingerprint, now),
                )
                conn.execute(
                    "UPDATE pairing_sessions SET state = 'completed', completed_at = ? "
                    "WHERE pairing_id = ?",
                    (now, pairing_id),
                )
                self._audit(
                    conn,
                    row["owner_user_id"],
                    row["device_id"],
                    "pairing_completed",
                    "success",
                )
        if proof_invalid:
            raise IdentityError("proof_invalid")
        return await self._issue_token(str(row["device_id"]))

    async def create_challenge(self, device_id: str) -> dict[str, str]:
        challenge_id = new_id("challenge")
        challenge = secrets.token_urlsafe(32)
        now = utc_now()
        expires_at = _future(self.challenge_ttl_seconds)
        with self.database.transaction() as conn:
            conn.execute(
                "DELETE FROM device_challenges WHERE used_at IS NOT NULL OR expires_at <= ?",
                (now,),
            )
            conn.execute(
                "DELETE FROM agent_access_tokens WHERE used_at IS NOT NULL OR expires_at <= ?",
                (now,),
            )
            row = conn.execute(
                "SELECT revoked_at FROM device_credentials WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is None:
                raise IdentityError("not_found")
            if row["revoked_at"] is not None:
                raise IdentityError("credential_revoked")
            conn.execute(
                "DELETE FROM device_challenges WHERE device_id = ?",
                (device_id,),
            )
            conn.execute(
                """
                INSERT INTO device_challenges(
                    challenge_id, device_id, challenge_hash, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (challenge_id, device_id, _hash(challenge), expires_at, now),
            )
        return {
            "challenge_id": challenge_id,
            "challenge": challenge,
            "expires_at": expires_at,
        }

    async def exchange_challenge(
        self, *, device_id: str, challenge_id: str, challenge: str, signature: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as conn:
            row = conn.execute(
                """
                SELECT c.*, d.public_key
                FROM device_challenges c
                JOIN device_credentials d ON d.device_id = c.device_id
                WHERE c.challenge_id = ? AND c.device_id = ? AND c.used_at IS NULL
                  AND d.revoked_at IS NULL
                """,
                (challenge_id, device_id),
            ).fetchone()
            if (
                row is None
                or row["expires_at"] <= now
                or not secrets.compare_digest(str(row["challenge_hash"]), _hash(challenge))
            ):
                raise IdentityError("not_found")
            _verify(
                str(row["public_key"]),
                signature,
                f"cad.challenge/1:{device_id}:{challenge_id}:{challenge}",
            )
            changed = conn.execute(
                "UPDATE device_challenges SET used_at = ? "
                "WHERE challenge_id = ? AND used_at IS NULL",
                (now, challenge_id),
            ).rowcount
            if changed != 1:
                raise IdentityError("not_found")
        return await self._issue_token(device_id)

    async def consume_access_token(self, token: str) -> str:
        now = utc_now()
        with self.database.transaction() as conn:
            row = conn.execute(
                """
                SELECT t.device_id
                FROM agent_access_tokens t
                JOIN device_credentials d ON d.device_id = t.device_id
                WHERE t.token_hash = ? AND t.used_at IS NULL AND t.expires_at > ?
                  AND d.revoked_at IS NULL
                """,
                (_hash(token), now),
            ).fetchone()
            if row is None:
                raise IdentityError("auth_failed")
            changed = conn.execute(
                "UPDATE agent_access_tokens SET used_at = ? "
                "WHERE token_hash = ? AND used_at IS NULL",
                (now, _hash(token)),
            ).rowcount
            if changed != 1:
                raise IdentityError("auth_failed")
        return str(row["device_id"])

    async def verify_hello(self, device_id: str, hello: Any, token: str) -> bool:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT public_key FROM device_credentials "
                "WHERE device_id = ? AND revoked_at IS NULL",
                (device_id,),
            ).fetchone()
        if row is None:
            return False
        try:
            _verify(
                str(row["public_key"]),
                str(getattr(hello, "device_proof", "")),
                f"cad.agent/2:{device_id}:{hello.message_id}:{token}",
            )
        except IdentityError:
            return False
        return True

    async def is_active_device(self, device_id: str) -> bool:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM device_credentials "
                "WHERE device_id = ? AND revoked_at IS NULL",
                (device_id,),
            ).fetchone()
        return row is not None

    async def revoke(self, *, owner_user_id: str, device_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM devices WHERE device_id = ? AND owner_subject = ?",
                (device_id, owner_user_id),
            ).fetchone()
            if row is None:
                raise IdentityError("not_found")
            conn.execute(
                "UPDATE device_credentials SET revoked_at = ? "
                "WHERE device_id = ? AND revoked_at IS NULL",
                (now, device_id),
            )
            conn.execute(
                "UPDATE agent_access_tokens SET used_at = COALESCE(used_at, ?) "
                "WHERE device_id = ?",
                (now, device_id),
            )
            conn.execute(
                "UPDATE device_challenges SET used_at = COALESCE(used_at, ?) "
                "WHERE device_id = ?",
                (now, device_id),
            )
            conn.execute(
                "UPDATE devices SET status = 'offline', updated_at = ? WHERE device_id = ?",
                (now, device_id),
            )
            conn.execute(
                "UPDATE agent_sessions SET disconnected_at = COALESCE(disconnected_at, ?) "
                "WHERE device_id = ?",
                (now, device_id),
            )
            self._audit(conn, owner_user_id, device_id, "device_revoked", "success")
        await self.registry.close_device(device_id, code=4403, reason="device revoked")

    async def _issue_token(self, device_id: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        now = utc_now()
        expires_at = _future(self.token_ttl_seconds)
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO agent_access_tokens(token_hash, device_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (_hash(token), device_id, expires_at, now),
            )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": self.token_ttl_seconds,
        }

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        owner_user_id: str | None,
        device_id: str | None,
        event_type: str,
        outcome: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO security_audit(
                audit_id, owner_user_id, device_id, event_type, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("audit"), owner_user_id, device_id, event_type, outcome, utc_now()),
        )

    @staticmethod
    def _pairing_by_reference(
        conn: sqlite3.Connection, reference: str
    ) -> sqlite3.Row | None:
        if len(reference) == 8:
            return conn.execute(
                "SELECT * FROM pairing_sessions WHERE user_code_hash = ?",
                (_hash(reference.upper()),),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM pairing_sessions WHERE pairing_id = ?", (reference,)
        ).fetchone()

    @staticmethod
    def _portal_device(row: sqlite3.Row, *, is_default: bool) -> dict[str, Any]:
        return {
            "id": str(row["device_id"]),
            "name": str(row["display_name"]),
            "is_default": is_default,
            "connected": row["status"] == "online",
            "last_seen_at": row["runtime_updated_at"],
            "runtime": None,
        }
