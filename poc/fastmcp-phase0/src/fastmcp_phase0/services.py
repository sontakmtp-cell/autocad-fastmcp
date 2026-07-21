"""Small in-memory application service layer used by the spike."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.ezdxf_backend import EzdxfBackend

from .contracts import (
    CONTRACT_VERSION,
    ArtifactRef,
    CadGetJobInput,
    CadGetJobOutput,
    CadListDevicesInput,
    CadListDevicesOutput,
    CadObserveInput,
    CadObserveOutput,
    DeviceInfo,
    JobError,
)


@dataclass(frozen=True)
class Principal:
    """Identity passed from the MCP boundary into the domain service."""

    subject: str
    scopes: tuple[str, ...]


class Phase0Services:
    """Fresh-per-test fake store with one headless DXF fixture."""

    def __init__(self) -> None:
        self.backend = EzdxfBackend()
        self.calls: list[dict[str, str]] = []
        self.force_backend_error = False
        self.raise_unexpected = False
        self._preview_png: bytes = b""
        self._initialized = False

    async def initialize(self) -> None:
        result = await self.backend.initialize()
        if not result.ok:
            raise RuntimeError("failed to initialize DXF fixture")
        await self.backend.create_line(0, 0, 100, 0)
        await self.backend.create_circle(50, 25, 10)
        screenshot = await self.backend.get_screenshot()
        if screenshot.ok and isinstance(screenshot.payload, str):
            self._preview_png = base64.b64decode(screenshot.payload)
        self._initialized = True

    def _record(self, operation: str, principal: Principal, correlation_id: str) -> None:
        self.calls.append(
            {
                "operation": operation,
                "subject": principal.subject,
                "correlation_id": correlation_id,
                "scopes": ",".join(principal.scopes),
            }
        )

    def _forced_failure(self) -> CommandResult | None:
        if self.raise_unexpected:
            raise RuntimeError("fixture failure with an implementation path")
        if self.force_backend_error:
            return CommandResult(
                ok=False,
                error="backend fixture failed at an internal path",
                error_code="backend_error",
            )
        if not self._initialized:
            return CommandResult(ok=False, error="fixture is not initialized", error_code="backend_error")
        return None

    async def list_devices(
        self,
        request: CadListDevicesInput,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_list_devices", principal, correlation_id)
        failure = self._forced_failure()
        if failure:
            return failure
        devices = [
            DeviceInfo(
                device_id="cad-online-01",
                display_name="AutoCAD fixture online",
                status="online",
                capabilities=["observe", "screenshot"],
            ),
            DeviceInfo(
                device_id="cad-offline-01",
                display_name="AutoCAD fixture offline",
                status="offline",
                capabilities=["observe"],
            ),
        ]
        if request.online_only:
            devices = [device for device in devices if device.status == "online"]
        if request.capability:
            devices = [device for device in devices if request.capability in device.capabilities]
        output = CadListDevicesOutput(
            correlation_id=correlation_id,
            devices=devices,
            default_device_id="cad-online-01" if devices else None,
        )
        return CommandResult(ok=True, payload=output.model_dump(mode="json"))

    async def observe(
        self,
        request: CadObserveInput,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_observe", principal, correlation_id)
        failure = self._forced_failure()
        if failure:
            return failure
        if request.device_id not in {"cad-online-01", "cad-offline-01"}:
            return CommandResult(ok=False, error="device does not exist", error_code="not_found")
        snapshot_id = f"snapshot-{request.device_id}"
        artifact_id = f"artifact-{request.device_id}-preview"
        output = CadObserveOutput(
            correlation_id=correlation_id,
            device_id=request.device_id,
            snapshot_id=snapshot_id,
            document_revision="revision-001",
            summary_uri=f"cad://snapshots/{snapshot_id}/summary",
            artifact_refs=[
                ArtifactRef(
                    artifact_id=artifact_id,
                    uri=f"cad://artifacts/{artifact_id}",
                    mime_type="image/png",
                )
            ],
        )
        return CommandResult(ok=True, payload=output.model_dump(mode="json"))

    async def get_job(
        self,
        request: CadGetJobInput,
        principal: Principal,
        correlation_id: str,
    ) -> CommandResult:
        self._record("cad_get_job", principal, correlation_id)
        failure = self._forced_failure()
        if failure:
            return failure
        jobs = {
            "job-completed-01": CadGetJobOutput(
                correlation_id=correlation_id,
                job_id=request.job_id,
                state="completed",
                progress=1.0,
                result={"snapshot_id": "snapshot-cad-online-01"},
                next_cursor=None,
            ),
            "job-running-01": CadGetJobOutput(
                correlation_id=correlation_id,
                job_id=request.job_id,
                state="running",
                progress=0.5,
                result=None,
                next_cursor=None if request.event_cursor == "cursor-001" else "cursor-001",
            ),
        }
        output = jobs.get(request.job_id)
        if output is None:
            return CommandResult(ok=False, error="job does not exist", error_code="not_found")
        return CommandResult(ok=True, payload=output.model_dump(mode="json"))

    async def read_snapshot(self, snapshot_id: str, principal: Principal) -> CommandResult:
        if snapshot_id not in {"snapshot-cad-online-01", "snapshot-cad-offline-01"}:
            return CommandResult(ok=False, error="snapshot does not exist", error_code="not_found")
        summary = {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": snapshot_id,
            "document_revision": "revision-001",
            "entity_summary": {"LINE": 1, "CIRCLE": 1},
        }
        return CommandResult(ok=True, payload=json.dumps(summary, sort_keys=True))

    async def read_artifact(self, artifact_id: str, principal: Principal) -> CommandResult:
        if artifact_id not in {
            "artifact-cad-online-01-preview",
            "artifact-cad-offline-01-preview",
        }:
            return CommandResult(ok=False, error="artifact does not exist", error_code="not_found")
        return CommandResult(ok=True, payload=self._preview_png)
