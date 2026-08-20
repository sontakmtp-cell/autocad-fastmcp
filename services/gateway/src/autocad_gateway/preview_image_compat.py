"""Durable read-only preview image support for ``cad_observe``.

The historical durable C1 path exposed ``include_preview_image`` in the MCP
schema but deliberately rejected ``true``.  The Managed R25 host now produces a
bounded PNG viewport snapshot, and the Desktop Agent carries it as chunked
base64 so no individual ``cad.agent`` JSON string exceeds the protocol limit.

This compatibility seam keeps the existing durable schema/storage model.  A
preview is stored inside the already owner-scoped terminal observe job result;
``cad://artifacts/<id>`` reconstructs and validates the PNG on read, so no temp
files or new persistence tables are required.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import durable_services as durable_module
from .application.job_service import DurableJobError, DurableJobService
from .contracts import (
    ArtifactRef,
    CadObserveInput,
    CadObserveInputDurable,
    CadObserveOutputC1,
    CadObserveOutputDurable,
    ExecutionEvidence,
    PackageEvidence,
    Principal,
    RevisionEvidence,
)
from .durable_services import DurableGatewayServices
from .services import GatewayError

PREVIEW_CAPABILITY = "cad.observe.preview-image/1"
PREVIEW_ARTIFACT_PREFIX = "artifact-observe-"
PREVIEW_MIME_TYPE = "image/png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PREVIEW_IMAGE_BYTES = 250_000
MAX_PREVIEW_CHUNKS = 8
MAX_PREVIEW_CHUNK_BYTES = 60_000

_INSTALLED = False
_ORIGINAL_OBSERVE = DurableGatewayServices.observe
_ORIGINAL_READ_ARTIFACT = DurableGatewayServices.read_artifact
_ORIGINAL_VALIDATE_C1 = DurableJobService._validate_c1_observation
_ORIGINAL_SAFE_AGENT_ERROR = DurableJobService._safe_agent_error


def _preview_bytes(value: Any) -> bytes:
    if not isinstance(value, dict):
        raise GatewayError("preview_unavailable")
    if set(value) != {
        "mime_type",
        "width",
        "height",
        "byte_count",
        "data_base64_chunks",
    }:
        raise GatewayError("preview_unavailable")
    width = value.get("width")
    height = value.get("height")
    byte_count = value.get("byte_count")
    chunks = value.get("data_base64_chunks")
    if (
        value.get("mime_type") != PREVIEW_MIME_TYPE
        or isinstance(width, bool)
        or not isinstance(width, int)
        or not 0 < width <= 1024
        or isinstance(height, bool)
        or not isinstance(height, int)
        or not 0 < height <= 1024
        or isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or not 0 < byte_count <= MAX_PREVIEW_IMAGE_BYTES
        or not isinstance(chunks, list)
        or not 1 <= len(chunks) <= MAX_PREVIEW_CHUNKS
        or any(
            not isinstance(chunk, str)
            or not chunk
            or len(chunk.encode("ascii", errors="ignore")) != len(chunk)
            or len(chunk) > MAX_PREVIEW_CHUNK_BYTES
            for chunk in chunks
        )
    ):
        raise GatewayError("preview_unavailable")
    try:
        data = base64.b64decode("".join(chunks), validate=True)
    except (binascii.Error, ValueError):
        raise GatewayError("preview_unavailable") from None
    if (
        len(data) != byte_count
        or len(data) > MAX_PREVIEW_IMAGE_BYTES
        or not data.startswith(PNG_SIGNATURE)
    ):
        raise GatewayError("preview_unavailable")
    return data


def _preview_ref(job_id: str) -> ArtifactRef:
    artifact_id = PREVIEW_ARTIFACT_PREFIX + job_id
    return ArtifactRef(
        artifact_id=artifact_id,
        uri=f"cad://artifacts/{artifact_id}",
        mime_type=PREVIEW_MIME_TYPE,
    )


def _validate_c1_with_preview(
    self: DurableJobService,
    result: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_package: dict[str, str] | None = None,
) -> str | None:
    preview = result.get("preview_image") if isinstance(result, dict) else None
    if preview is None:
        return _ORIGINAL_VALIDATE_C1(
            self,
            result,
            snapshot,
            expected_package=expected_package,
        )
    try:
        _preview_bytes(preview)
    except GatewayError:
        return "preview_unavailable"
    without_preview = {key: item for key, item in result.items() if key != "preview_image"}
    return _ORIGINAL_VALIDATE_C1(
        self,
        without_preview,
        snapshot,
        expected_package=expected_package,
    )


def _safe_agent_error_with_preview(error_code: str | None) -> tuple[str, str]:
    if error_code == "preview_unavailable":
        return "preview_unavailable", "Agent could not capture a valid bounded PNG preview"
    return _ORIGINAL_SAFE_AGENT_ERROR(error_code)


async def _observe_with_preview(
    self: DurableGatewayServices,
    request: CadObserveInput | CadObserveInputDurable,
    principal: Principal,
    correlation_id: str,
) -> CadObserveOutputDurable | CadObserveOutputC1:
    if not request.include_preview_image:
        return await _ORIGINAL_OBSERVE(self, request, principal, correlation_id)

    device = await self._require_device(request.device_id, principal)
    capabilities = set(device.get("capabilities", ()))
    if "observe" not in capabilities or PREVIEW_CAPABILITY not in capabilities:
        raise GatewayError("capability_missing")

    payload: dict[str, Any] = {
        "observation_level": request.observation_level,
        "include_preview_image": True,
    }
    if self.is_phase5_identity:
        observation_packages = [
            package
            for package in device.get("packages", [])
            if package.get("package_id") == "autocad.lisp.drawing_info"
        ]
        if len(observation_packages) != 1:
            raise GatewayError("package_mismatch")
        payload["package"] = observation_packages[0]
    elif self.is_phase4:
        payload["package"] = self.required_package

    explicit_key = getattr(request, "idempotency_key", None)
    key = explicit_key or f"observe-{uuid.uuid4()}"
    deadline_at = (
        datetime.now(timezone.utc) + timedelta(seconds=self.job_deadline_seconds)
    ).isoformat()
    try:
        job = await self.job_service.create_and_observe(
            owner_subject=principal.subject,
            device_id=request.device_id,
            payload=payload,
            correlation_id=correlation_id,
            idempotency_key=key,
            deadline_at=deadline_at,
        )
    except DurableJobError as error:
        raise GatewayError(
            self._safe_job_error_code(error.code),
            job_id=error.job_id,
            job_state=error.job_state,
        ) from None

    if job["state"] != "succeeded":
        code = (
            "job_in_progress"
            if job["state"]
            in {
                "queued",
                "dispatched",
                "acknowledged",
                "running",
                "cancel_requested",
                "reconnect_pending",
                "outcome_unknown",
            }
            else self._safe_job_error_code(job.get("error_code"))
        )
        raise GatewayError(
            code,
            job_id=job["job_id"],
            job_state=job["state"],
        )

    result = job.get("result")
    if not isinstance(result, dict):
        raise GatewayError(
            "backend_error",
            job_id=job["job_id"],
            job_state=job["state"],
        )
    snapshot = result.get("snapshot")
    if not isinstance(snapshot, dict):
        raise GatewayError("backend_error")
    _preview_bytes(result.get("preview_image"))
    artifact_refs = [_preview_ref(job["job_id"])]
    entity_count = int(
        snapshot.get("entity_summary", {}).get(
            "entity_count", len(snapshot.get("entities", []))
        )
    )

    if self.is_phase4:
        evidence = result.get("execution_evidence", {})
        package = evidence.get("package") or self.required_package
        return CadObserveOutputC1(
            correlation_id=correlation_id,
            device_id=request.device_id,
            snapshot_id=str(snapshot["snapshot_id"]),
            document_revision=str(snapshot["document_revision"]),
            observation_level=request.observation_level,
            entity_count=entity_count,
            summary_uri=f"cad://snapshots/{snapshot['snapshot_id']}/summary",
            entities_uri=f"cad://snapshots/{snapshot['snapshot_id']}/entities",
            artifact_refs=artifact_refs,
            job_id=job["job_id"],
            revision_evidence=RevisionEvidence.model_validate(
                snapshot.get("revision_evidence", {})
            ),
            execution_evidence=ExecutionEvidence(
                agent_version=str(evidence.get("agent_version", "unknown")),
                command_id=job["command_id"],
                package=PackageEvidence.model_validate(package),
                runtime_state=evidence.get("runtime_state"),
            ),
        )

    return CadObserveOutputDurable(
        correlation_id=correlation_id,
        device_id=request.device_id,
        snapshot_id=str(snapshot["snapshot_id"]),
        document_revision=str(snapshot["document_revision"]),
        observation_level=request.observation_level,
        entity_count=entity_count,
        summary_uri=f"cad://snapshots/{snapshot['snapshot_id']}/summary",
        entities_uri=f"cad://snapshots/{snapshot['snapshot_id']}/entities",
        artifact_refs=artifact_refs,
        job_id=job["job_id"],
    )


async def _read_preview_artifact(
    self: DurableGatewayServices,
    artifact_id: str,
    principal: Principal,
) -> bytes:
    if not isinstance(artifact_id, str) or not artifact_id.startswith(PREVIEW_ARTIFACT_PREFIX):
        return await _ORIGINAL_READ_ARTIFACT(self, artifact_id, principal)
    job_id = artifact_id[len(PREVIEW_ARTIFACT_PREFIX) :]
    if not job_id or len(job_id) > 128:
        raise GatewayError("not_found")
    job = await self.repository.get_job(principal.subject, job_id)
    if (
        job is None
        or job.get("kind") != "observe"
        or job.get("state") != "succeeded"
        or not isinstance(job.get("result"), dict)
    ):
        raise GatewayError("not_found")
    return _preview_bytes(job["result"].get("preview_image"))


def install_preview_image_compat() -> None:
    """Install bounded durable viewport previews once."""

    global _INSTALLED
    if _INSTALLED:
        return
    DurableJobService._validate_c1_observation = _validate_c1_with_preview
    DurableJobService._safe_agent_error = staticmethod(_safe_agent_error_with_preview)
    DurableGatewayServices.observe = _observe_with_preview
    DurableGatewayServices.read_artifact = _read_preview_artifact
    durable_module._SAFE_JOB_ERROR_CODES = (
        durable_module._SAFE_JOB_ERROR_CODES | {"preview_unavailable"}
    )
    _INSTALLED = True
