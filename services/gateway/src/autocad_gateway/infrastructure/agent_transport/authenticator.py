"""Fixture-only Agent authentication for the Phase 3 POC."""

from __future__ import annotations


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

    @property
    def device_ids(self) -> tuple[str, ...]:
        return tuple(self._tokens)
