"""Additive runtime and capability evidence shared across Agent boundaries."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .agent_protocol import (
    MAX_CAPABILITIES,
    canonical_capabilities,
    canonical_json,
    validate_bounded_json,
)


CAPABILITY_MANIFEST_SCHEMA = "cad.capability/1"
MAX_PRODUCTS = 16
MAX_FALLBACK_RUNTIMES = 8
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256 = r"^(?:sha256:)?[0-9a-f]{64}$"


class RuntimeContract(BaseModel):
    """Forward-compatible bounded model.

    Unknown optional fields are retained so a newer manifest has one canonical
    representation and one hash when handled by older Phase 5 readers.
    """

    model_config = ConfigDict(extra="allow", strict=True)

    @model_validator(mode="after")
    def _bounded_json(self) -> "RuntimeContract":
        validate_bounded_json(self.model_dump(mode="json", exclude_none=True))
        return self


class RuntimeEvidence(RuntimeContract):
    id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    role: Literal["primary", "compatibility", "compatibility_fallback", "headless"]
    host_family: str | None = Field(default=None, min_length=1, max_length=32)
    host_version: str | None = Field(default=None, min_length=1, max_length=64)
    framework: str | None = Field(default=None, min_length=1, max_length=64)
    package_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER.pattern,
    )
    package_version: str | None = Field(default=None, min_length=1, max_length=64)
    package_hash: str | None = Field(default=None, pattern=_SHA256)


class CadProductManifest(RuntimeContract):
    product: str = Field(min_length=1, max_length=128)
    edition: Literal["full", "lt", "headless"]
    release_year: int | None = Field(default=None, ge=2000, le=2200)
    series: str | None = Field(default=None, min_length=1, max_length=32)
    vertical: str | None = Field(default=None, min_length=1, max_length=128)
    runtime: RuntimeEvidence
    capabilities: list[str] = Field(default_factory=list, max_length=MAX_CAPABILITIES)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _canonicalize_capabilities(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("capabilities must be a list")
        return list(canonical_capabilities(value))


class FallbackRuntimeManifest(RuntimeContract):
    id: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    role: Literal["compatibility", "headless"]
    package_version: str | None = Field(default=None, min_length=1, max_length=64)
    package_hash: str | None = Field(default=None, pattern=_SHA256)


class CapabilityManifest(RuntimeContract):
    schema_version: Literal["cad.capability/1"] = CAPABILITY_MANIFEST_SCHEMA
    registry_version: str = Field(min_length=1, max_length=64)
    cad_products: list[CadProductManifest] = Field(default_factory=list, max_length=MAX_PRODUCTS)
    fallback_runtimes: list[FallbackRuntimeManifest] = Field(
        default_factory=list,
        max_length=MAX_FALLBACK_RUNTIMES,
    )

    @model_validator(mode="after")
    def _runtime_entries_are_unique(self) -> "CapabilityManifest":
        product_keys = [
            (
                item.product.casefold(),
                item.edition,
                item.release_year,
                item.runtime.id,
            )
            for item in self.cad_products
        ]
        if len(product_keys) != len(set(product_keys)):
            raise ValueError("CAD product runtime entries must be unique")
        fallback_ids = [item.id for item in self.fallback_runtimes]
        if len(fallback_ids) != len(set(fallback_ids)):
            raise ValueError("fallback runtime entries must be unique")
        return self


def canonical_capability_manifest(
    manifest: CapabilityManifest | dict[str, Any],
) -> dict[str, Any]:
    value = (
        manifest
        if isinstance(manifest, CapabilityManifest)
        else CapabilityManifest.model_validate(manifest)
    )
    products = sorted(
        value.cad_products,
        key=lambda item: (
            item.product.casefold(),
            item.edition,
            item.release_year or 0,
            item.runtime.id,
        ),
    )
    fallbacks = sorted(value.fallback_runtimes, key=lambda item: (item.id, item.role))
    result = value.model_dump(mode="json", exclude_none=True, exclude={"cad_products", "fallback_runtimes"})
    result["cad_products"] = [
        item.model_dump(mode="json", exclude_none=True) for item in products
    ]
    result["fallback_runtimes"] = [
        item.model_dump(mode="json", exclude_none=True) for item in fallbacks
    ]
    validate_bounded_json(result)
    return result


def canonical_capability_manifest_hash(
    manifest: CapabilityManifest | dict[str, Any],
) -> str:
    value = canonical_capability_manifest(manifest)
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
