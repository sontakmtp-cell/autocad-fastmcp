"""Additive Phase 5 identity and device-pairing contracts."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_PAIRING_CODE = re.compile(r"^[A-Z2-9]{8}$")


class IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PairingStartRequest(IdentityModel):
    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    public_key: str = Field(min_length=43, max_length=44)

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str) -> str:
        if not _PUBLIC_ID.fullmatch(value):
            raise ValueError("device_id is invalid")
        return value


class PairingStartResponse(IdentityModel):
    pairing_id: str
    user_code: str
    challenge: str
    polling_secret: str
    confirmation_url: str
    expires_at: str


class PairingApproveRequest(IdentityModel):
    user_code: str

    @field_validator("user_code")
    @classmethod
    def validate_user_code(cls, value: str) -> str:
        canonical = value.strip().upper()
        if not _PAIRING_CODE.fullmatch(canonical):
            raise ValueError("user_code is invalid")
        return canonical


class PairingCompleteRequest(IdentityModel):
    pairing_id: str | None = Field(default=None, min_length=1, max_length=128)
    challenge: str = Field(min_length=32, max_length=128)
    signature: str = Field(min_length=86, max_length=88)


class DeviceChallengeRequest(IdentityModel):
    device_id: str = Field(min_length=1, max_length=128)


class DeviceChallengeResponse(IdentityModel):
    challenge_id: str
    challenge: str
    expires_at: str


class DeviceTokenRequest(IdentityModel):
    device_id: str = Field(min_length=1, max_length=128)
    challenge_id: str = Field(min_length=1, max_length=128)
    challenge: str = Field(min_length=32, max_length=128)
    signature: str = Field(min_length=86, max_length=88)


class DeviceTokenResponse(IdentityModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class DeviceRevokeRequest(IdentityModel):
    device_id: str = Field(min_length=1, max_length=128)
