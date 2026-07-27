"""Phase 6 owner-scoped CAD Program application service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from autocad_contracts import (
    canonical_json,
    canonical_preview_digest,
    canonical_receipt_id,
    normalize_sha256_digest,
)

from .application.job_service import DurableJobError, DurableJobService
from .contracts import (
    CadCommitInput,
    CadCommitOutput,
    CadPrepareProgramInput,
    CadPrepareProgramOutput,
    CadPreviewInput,
    CadPreviewOutput,
    CadValidateInput,
    CadValidateOutput,
    Principal,
)
from .infrastructure.agent_transport.connection_registry import ConnectionRegistry
from .infrastructure.sqlite.program_repository import ProgramRepository
from .infrastructure.sqlite.repositories import RepositoryConflict, SqliteRepository
from .program_contract_adapter import (
    ProgramContractError,
    binding_digest,
    build_semantic_program,
    canonical_digest,
    canonical_program_digest_value,
    execution_digest,
    operation_registry_digest_value,
    program_command_fields,
    validate_pin_set,
)
from .services import GatewayError


@dataclass(frozen=True)
class ProgramGatewayPolicy:
    program_enabled: bool = False
    managed_write_enabled: bool = False
    allowed_device_ids: tuple[str, ...] = ()
    policy_version: str = "phase6-policy/1"
    job_deadline_seconds: float = 300.0


class ProgramGatewayService:
    def __init__(
        self,
        repository: SqliteRepository,
        program_repository: ProgramRepository,
        registry: ConnectionRegistry,
        job_service: DurableJobService,
        policy: ProgramGatewayPolicy,
    ) -> None:
        self.repository = repository
        self.program_repository = program_repository
        self.registry = registry
        self.job_service = job_service
        self.policy = policy
        self.allowed_device_ids = frozenset(policy.allowed_device_ids)

    async def prepare(
        self,
        request: CadPrepareProgramInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadPrepareProgramOutput:
        self._require_write_scope(principal)
        self._require_program_enabled()
        device = await self.repository.get_device(principal.subject, request.device_id)
        if device is None:
            raise GatewayError("not_found")
        snapshot = await self.repository.get_snapshot(
            principal.subject, request.source_snapshot_id
        )
        if snapshot is None or snapshot["device_id"] != request.device_id:
            raise GatewayError("not_found")
        revision_evidence = snapshot.get("revision_evidence") or {}
        if (
            revision_evidence.get("commit_safe") is not True
            or revision_evidence.get("revision_strength") in {None, "summary_only"}
        ):
            raise GatewayError("stale_snapshot")
        connection, pins = await self._current_binding(
            owner_subject=principal.subject,
            device_id=request.device_id,
        )
        document_id = self._snapshot_document_id(snapshot)
        if (
            connection.active_document_id != document_id
            or connection.active_document_revision != snapshot["document_revision"]
        ):
            raise GatewayError("stale_snapshot")
        program_id = f"program-{uuid.uuid4()}"
        try:
            semantic, _, required_capabilities = build_semantic_program(
                program_id=program_id,
                program_revision=1,
                device_id=request.device_id,
                source_snapshot_id=request.source_snapshot_id,
                document_id=document_id,
                expected_document_revision=snapshot["document_revision"],
                registry_version=pins["registry_version"],
                operations=request.operations,
                postconditions=request.postconditions,
                budget_overrides=request.budget_overrides,
            )
        except ProgramContractError as error:
            raise GatewayError("invalid_request") from error
        capabilities = set(connection.capabilities)
        missing = sorted(set(required_capabilities) - capabilities)
        program_digest = canonical_program_digest_value(semantic)
        idempotency_key = request.idempotency_key or f"prepare-{uuid.uuid4()}"
        request_hash = canonical_digest(
            {
                "action": "prepare",
                "owner_subject": principal.subject,
                "device_id": request.device_id,
                "source_snapshot_id": request.source_snapshot_id,
                "operations": request.operations,
                "postconditions": request.postconditions,
                "budget_overrides": request.budget_overrides,
            }
        )
        try:
            stored, duplicate = await self.program_repository.create_program(
                owner_subject=principal.subject,
                program_id=program_id,
                device_id=request.device_id,
                document_id=document_id,
                source_snapshot_id=request.source_snapshot_id,
                expected_document_revision=snapshot["document_revision"],
                semantic=semantic,
                program_digest=program_digest,
                pins=pins,
                risk_class="low",
                missing_capabilities=missing,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None
        del duplicate
        ready = (
            not stored["missing_capabilities"]
            and self.policy.managed_write_enabled
            and request.device_id in self.allowed_device_ids
            and connection.write_lock_enabled
            and not connection.hard_pause
            and not connection.paused
        )
        return CadPrepareProgramOutput(
            correlation_id=correlation_id,
            program_id=stored["program_id"],
            program_revision=stored["program_revision"],
            program_digest=stored["program_digest"],
            document_id=stored["document_id"],
            expected_document_revision=stored["expected_document_revision"],
            execution_binding=stored["pins"],
            risk_class="low",
            missing_capabilities=stored["missing_capabilities"],
            resource_uri=(
                f"cad://programs/{stored['program_id']}/revisions/"
                f"{stored['program_revision']}"
            ),
            ready_for_preview=ready,
        )

    async def preview(
        self,
        request: CadPreviewInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadPreviewOutput:
        self._require_write_scope(principal)
        self._require_managed_write()
        program = await self._require_program(
            principal.subject, request.program_id, request.program_revision
        )
        self._require_allowed_device(program["device_id"])
        if program["missing_capabilities"]:
            raise GatewayError("capability_missing")
        connection = await self._revalidate_program(program)
        if not connection.write_lock_enabled or connection.hard_pause or connection.paused:
            raise GatewayError("write_lock_disabled")
        preview_id = f"preview-{uuid.uuid4()}"
        binding = binding_digest(
            program_digest=program["program_digest"],
            document_id=program["document_id"],
            expected_document_revision=program["expected_document_revision"],
            pins=program["pins"],
        )
        digest = execution_digest(
            action="preview",
            program_digest=program["program_digest"],
            binding_digest_value=binding,
            nonce_id=preview_id,
        )
        ttl = int(program["semantic"]["budgets"]["preview_ttl_seconds"])
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        ).isoformat()
        payload = self._job_payload(
            program,
            action="preview",
            execution={
                "preview_id": preview_id,
                "execution_digest": digest,
                "binding_digest": binding,
                "expires_at": expires_at,
            },
        )
        preview_fields = program_command_fields(
            kind="program_preview",
            effect_class="write",
            payload=payload,
        )
        payload["execution"]["preview_digest"] = canonical_preview_digest(
            preview_id,
            preview_fields["binding"],
        )
        job, _ = await self._create_job(
            program,
            action="preview",
            payload=payload,
            idempotency_key=request.idempotency_key or f"preview-{uuid.uuid4()}",
            acquire_write_lock=True,
        )
        job = await self._wait(job, principal.subject, correlation_id)
        material = await self.program_repository.get_preview_by_job(
            principal.subject, job["job_id"]
        )
        execution = job["payload"]["execution"]
        return CadPreviewOutput(
            correlation_id=correlation_id,
            program_id=program["program_id"],
            program_revision=program["program_revision"],
            preview_id=material["preview_id"] if material else None,
            job_id=job["job_id"],
            state=job["state"],
            program_digest=program["program_digest"],
            execution_digest=execution["execution_digest"],
            binding_digest=execution["binding_digest"],
            planned_operation_count=(
                material["planned_operation_count"] if material else None
            ),
            planned_entity_count=material["planned_entity_count"] if material else None,
            planned_layer_count=material["planned_layer_count"] if material else None,
            validation=material["validation"] if material else None,
            expires_at=execution["expires_at"],
            job_uri=f"cad://jobs/{job['job_id']}",
            resource_uri=(
                f"cad://previews/{material['preview_id']}" if material else None
            ),
        )

    async def commit(
        self,
        request: CadCommitInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadCommitOutput:
        self._require_write_scope(principal)
        self._require_managed_write()
        preview = await self.program_repository.get_preview(
            principal.subject, request.preview_id
        )
        if preview is None:
            raise GatewayError("not_found")
        program = await self._require_program(
            principal.subject,
            preview["program_id"],
            preview["program_revision"],
        )
        self._require_allowed_device(program["device_id"])
        existing_receipt = await self.program_repository.get_receipt_by_preview(
            principal.subject, preview["preview_id"]
        )
        if existing_receipt is None:
            if preview["invalidated_reason"]:
                raise GatewayError("binding_mismatch")
            if self._expired(preview["expires_at"]):
                await self.program_repository.invalidate_preview(
                    preview["preview_id"], "preview_expired"
                )
                raise GatewayError("preview_expired")
            await self._revalidate_program(program, preview=preview)
        receipt_id = canonical_receipt_id(preview["preview_id"])
        digest = execution_digest(
            action="commit",
            program_digest=program["program_digest"],
            binding_digest_value=preview["binding_digest"],
            nonce_id=receipt_id,
        )
        payload = self._job_payload(
            program,
            action="commit",
            execution={
                "preview_id": preview["preview_id"],
                "receipt_id": receipt_id,
                "preview_execution_digest": preview["execution_digest"],
                "preview_digest": preview["preview_digest"],
                "execution_digest": digest,
                "binding_digest": preview["binding_digest"],
            },
        )
        job, duplicate = await self._create_job(
            program,
            action="commit",
            payload=payload,
            idempotency_key=request.idempotency_key or f"commit-{uuid.uuid4()}",
            acquire_write_lock=True,
        )
        prior_receipt = job.get("prior_receipt")
        if prior_receipt is not None:
            return self._commit_output(
                prior_receipt,
                correlation_id=correlation_id,
                duplicate=True,
            )
        job = await self._wait(job, principal.subject, correlation_id)
        receipt = await self.program_repository.get_receipt_by_job(
            principal.subject, job["job_id"]
        )
        if receipt is None:
            execution = job["payload"]["execution"]
            return CadCommitOutput(
                correlation_id=correlation_id,
                program_id=program["program_id"],
                program_revision=program["program_revision"],
                preview_id=preview["preview_id"],
                job_id=job["job_id"],
                state=job["state"],
                program_digest=program["program_digest"],
                execution_digest=execution["execution_digest"],
                binding_digest=preview["binding_digest"],
                document_revision_before=program["expected_document_revision"],
                duplicate=duplicate,
                job_uri=f"cad://jobs/{job['job_id']}",
            )
        return self._commit_output(
            receipt,
            correlation_id=correlation_id,
            duplicate=duplicate,
        )

    async def validate(
        self,
        request: CadValidateInput,
        principal: Principal,
        correlation_id: str,
    ) -> CadValidateOutput:
        self._require_write_scope(principal)
        self._require_program_enabled()
        receipt = await self.program_repository.get_receipt(
            principal.subject, request.receipt_id
        )
        if receipt is None:
            raise GatewayError("not_found")
        program = await self._require_program(
            principal.subject,
            receipt["program_id"],
            receipt["program_revision"],
        )
        await self._revalidate_program(
            program, expected_revision=receipt["document_revision_after"]
        )
        validation_id = f"validation-{uuid.uuid4()}"
        digest = execution_digest(
            action="validate",
            program_digest=program["program_digest"],
            binding_digest_value=receipt["binding_digest"],
            nonce_id=validation_id,
        )
        payload = self._job_payload(
            program,
            action="validate",
            execution={
                "receipt_id": receipt["receipt_id"],
                "validation_id": validation_id,
                "execution_digest": digest,
                "binding_digest": receipt["binding_digest"],
                "expected_document_revision": receipt["document_revision_after"],
            },
            validation={
                "validation_id": validation_id,
                "receipt_id": receipt["receipt_id"],
                "expected_entity_count": next(
                    (
                        item["expected_created"]
                        for item in program["semantic"]["postconditions"]
                        if item.get("kind") == "entity_count"
                    ),
                    None,
                ),
                "expected_entity_types": [],
                "expected_layers": [
                    item["layer"]
                    for item in program["semantic"]["postconditions"]
                    if item.get("kind") == "layer_exists"
                    and isinstance(item.get("layer"), str)
                ],
            },
        )
        job, _ = await self._create_job(
            program,
            action="validate",
            payload=payload,
            idempotency_key=request.idempotency_key or f"validate-{uuid.uuid4()}",
            acquire_write_lock=False,
        )
        job = await self._wait(job, principal.subject, correlation_id)
        validation = await self.program_repository.get_validation_by_job(
            principal.subject, job["job_id"]
        )
        execution = job["payload"]["execution"]
        return CadValidateOutput(
            correlation_id=correlation_id,
            program_id=program["program_id"],
            program_revision=program["program_revision"],
            receipt_id=receipt["receipt_id"],
            validation_id=validation["validation_id"] if validation else None,
            job_id=job["job_id"],
            state=job["state"],
            execution_digest=execution["execution_digest"],
            binding_digest=receipt["binding_digest"],
            passed=validation["passed"] if validation else None,
            report=validation["report"] if validation else None,
            job_uri=f"cad://jobs/{job['job_id']}",
            resource_uri=(
                f"cad://validations/{validation['validation_id']}"
                if validation
                else None
            ),
        )

    async def read_program(
        self, owner_subject: str, program_id: str, revision: int
    ) -> str:
        value = await self.program_repository.get_program_revision(
            owner_subject, program_id, revision
        )
        return self._bounded_resource(value)

    async def read_preview(self, owner_subject: str, preview_id: str) -> str:
        return self._bounded_resource(
            await self.program_repository.get_preview(owner_subject, preview_id)
        )

    async def read_receipt(self, owner_subject: str, receipt_id: str) -> str:
        return self._bounded_resource(
            await self.program_repository.get_receipt(owner_subject, receipt_id)
        )

    async def read_validation(self, owner_subject: str, validation_id: str) -> str:
        return self._bounded_resource(
            await self.program_repository.get_validation(owner_subject, validation_id)
        )

    async def _create_job(
        self,
        program: dict[str, Any],
        *,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
        acquire_write_lock: bool,
    ) -> tuple[dict[str, Any], bool]:
        deadline_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self.policy.job_deadline_seconds)
        ).isoformat()
        execution = payload["execution"]
        logical_request = {
            "action": action,
            "owner_subject": program["owner_subject"],
            "program_id": program["program_id"],
            "program_revision": program["program_revision"],
            "program_digest": program["program_digest"],
            **(
                {
                    "preview_id": execution["preview_id"],
                    "preview_execution_digest": execution[
                        "preview_execution_digest"
                    ],
                    "binding_digest": execution["binding_digest"],
                }
                if action == "commit"
                else {
                    "receipt_id": execution["receipt_id"],
                    "binding_digest": execution["binding_digest"],
                }
                if action == "validate"
                else {}
            ),
        }
        try:
            return await self.program_repository.create_action_job(
                owner_subject=program["owner_subject"],
                action=action,
                device_id=program["device_id"],
                document_id=program["document_id"],
                payload=payload,
                idempotency_key=idempotency_key,
                deadline_at=deadline_at,
                acquire_write_lock=acquire_write_lock,
                idempotency_request_hash=canonical_digest(logical_request),
            )
        except RepositoryConflict as error:
            raise GatewayError(self._repository_code(error.code)) from None

    async def _wait(
        self, job: dict[str, Any], owner_subject: str, correlation_id: str
    ) -> dict[str, Any]:
        try:
            value = await self.job_service.wait_for_existing_job(
                job,
                owner_subject=owner_subject,
                correlation_id=correlation_id,
            )
        except DurableJobError as error:
            raise GatewayError(
                self._repository_code(error.code),
                job_id=error.job_id,
                job_state=error.job_state,
            ) from None
        if value["state"] in {"failed", "cancelled", "needs_attention"}:
            raise GatewayError(
                self._repository_code(value.get("error_code") or value["state"]),
                job_id=value["job_id"],
                job_state=value["state"],
            )
        return value

    async def _require_program(
        self, owner_subject: str, program_id: str, revision: int
    ) -> dict[str, Any]:
        value = await self.program_repository.get_program_revision(
            owner_subject, program_id, revision
        )
        if value is None:
            raise GatewayError("not_found")
        return value

    async def _current_binding(
        self, *, owner_subject: str, device_id: str
    ) -> tuple[Any, dict[str, str]]:
        device = await self.repository.get_device(owner_subject, device_id)
        if device is None:
            raise GatewayError("not_found")
        connection = await self.registry.get(device_id)
        if (
            connection is None
            or not await self.registry.is_current_and_fresh(connection)
        ):
            raise GatewayError("device_offline")
        manifest = connection.capability_manifest
        if (
            not isinstance(manifest, dict)
            or not connection.capability_manifest_hash
            or not connection.operation_registry_hash
            or not connection.registry_version
        ):
            raise GatewayError("capability_missing")
        expected_registry_hash = operation_registry_digest_value()
        if (
            expected_registry_hash is not None
            and normalize_sha256_digest(connection.operation_registry_hash)
            != expected_registry_hash
        ):
            raise GatewayError("binding_mismatch")
        candidates = [
            item
            for item in manifest.get("cad_products", [])
            if isinstance(item, dict)
            and item.get("edition") == "full"
            and item.get("release_year") == 2025
            and isinstance(item.get("runtime"), dict)
            and item["runtime"].get("id") == "managed_dotnet"
            and item["runtime"].get("role") == "primary"
            and item["runtime"].get("host_family") == "R25"
        ]
        if len(candidates) != 1:
            raise GatewayError("capability_missing")
        runtime = candidates[0]["runtime"]
        package_id = runtime.get("package_id")
        package_version = runtime.get("package_version")
        package_hash = runtime.get("package_hash")
        if not all(
            isinstance(value, str) and value
            for value in (package_id, package_version, package_hash)
        ):
            raise GatewayError("package_mismatch")
        pins = {
            "runtime_id": runtime["id"],
            "runtime_role": runtime["role"],
            "host_family": runtime["host_family"],
            "host_version": runtime.get("host_version", ""),
            "package_id": package_id,
            "package_version": package_version,
            "package_hash": package_hash,
            "capability_manifest_hash": normalize_sha256_digest(
                connection.capability_manifest_hash
            ),
            "operation_registry_hash": normalize_sha256_digest(
                connection.operation_registry_hash
            ),
            "registry_version": connection.registry_version,
            "policy_version": self.policy.policy_version,
        }
        try:
            return connection, validate_pin_set(pins)
        except ProgramContractError as error:
            raise GatewayError("binding_mismatch") from error

    async def _revalidate_program(
        self,
        program: dict[str, Any],
        *,
        preview: dict[str, Any] | None = None,
        expected_revision: str | None | object = ...,
    ) -> Any:
        connection, current_pins = await self._current_binding(
            owner_subject=program["owner_subject"],
            device_id=program["device_id"],
        )
        if current_pins != program["pins"]:
            if preview is not None:
                await self.program_repository.invalidate_preview(
                    preview["preview_id"], "execution_binding_changed"
                )
            raise GatewayError("binding_mismatch")
        revision = (
            program["expected_document_revision"]
            if expected_revision is ...
            else expected_revision
        )
        if (
            connection.active_document_id != program["document_id"]
            or (
                revision is not None
                and connection.active_document_revision != revision
            )
        ):
            if preview is not None:
                await self.program_repository.invalidate_preview(
                    preview["preview_id"], "document_revision_changed"
                )
            raise GatewayError("stale_revision")
        if preview is not None:
            preview_pins = {
                key: preview[key]
                for key in (
                    "runtime_id",
                    "runtime_role",
                    "host_family",
                    "host_version",
                    "package_id",
                    "package_version",
                    "package_hash",
                    "capability_manifest_hash",
                    "operation_registry_hash",
                    "registry_version",
                    "policy_version",
                )
            }
            expected_binding = binding_digest(
                program_digest=program["program_digest"],
                document_id=program["document_id"],
                expected_document_revision=program["expected_document_revision"],
                pins=preview_pins,
            )
            if (
                preview["program_digest"] != program["program_digest"]
                or preview["binding_digest"] != expected_binding
                or preview_pins != program["pins"]
            ):
                await self.program_repository.invalidate_preview(
                    preview["preview_id"], "preview_binding_mismatch"
                )
                raise GatewayError("binding_mismatch")
        return connection

    @staticmethod
    def _snapshot_document_id(snapshot: dict[str, Any]) -> str:
        drawing = snapshot.get("drawing") or {}
        document_id = drawing.get("document_id")
        if document_id is None and isinstance(drawing.get("document_identity"), dict):
            document_id = drawing["document_identity"].get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise GatewayError("stale_snapshot")
        return document_id

    @staticmethod
    def _job_payload(
        program: dict[str, Any],
        *,
        action: str,
        execution: dict[str, Any],
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = {
            "action": action,
            "program_id": program["program_id"],
            "program_revision": program["program_revision"],
            "program_digest": program["program_digest"],
            "document_id": program["document_id"],
            "expected_document_revision": program["expected_document_revision"],
            "pins": program["pins"],
            **execution,
        }
        payload = {
            "program": program["semantic"],
            "execution": values,
            "package": {
                "package_id": program["pins"]["package_id"],
                "version": program["pins"]["package_version"],
                "sha256": program["pins"]["package_hash"].removeprefix("sha256:"),
            },
        }
        if validation is not None:
            payload["validation"] = validation
        return payload

    @staticmethod
    def _commit_output(
        receipt: dict[str, Any],
        *,
        correlation_id: str,
        duplicate: bool,
    ) -> CadCommitOutput:
        return CadCommitOutput(
            correlation_id=correlation_id,
            program_id=receipt["program_id"],
            program_revision=receipt["program_revision"],
            preview_id=receipt["preview_id"],
            receipt_id=receipt["receipt_id"],
            job_id=receipt["job_id"],
            state="succeeded",
            program_digest=receipt["program_digest"],
            execution_digest=receipt["execution_digest"],
            binding_digest=receipt["binding_digest"],
            document_revision_before=receipt["document_revision_before"],
            document_revision_after=receipt["document_revision_after"],
            effect_summary=receipt["effect_summary"],
            duplicate=duplicate,
            job_uri=f"cad://jobs/{receipt['job_id']}",
            resource_uri=f"cad://receipts/{receipt['receipt_id']}",
        )

    @staticmethod
    def _expired(value: str) -> bool:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return True
        return parsed <= datetime.now(timezone.utc)

    @staticmethod
    def _bounded_resource(value: dict[str, Any] | None) -> str:
        if value is None:
            raise GatewayError("not_found")
        encoded = canonical_json(value)
        if len(encoded.encode("utf-8")) > 256_000:
            raise GatewayError("response_too_large")
        return encoded

    def _require_program_enabled(self) -> None:
        if not self.policy.program_enabled:
            raise GatewayError("feature_disabled")

    def _require_managed_write(self) -> None:
        self._require_program_enabled()
        if not self.policy.managed_write_enabled:
            raise GatewayError("feature_disabled")

    def _require_allowed_device(self, device_id: str) -> None:
        if device_id not in self.allowed_device_ids:
            raise GatewayError("feature_disabled")

    @staticmethod
    def _require_write_scope(principal: Principal) -> None:
        if "autocad.write" not in principal.scopes:
            raise GatewayError("insufficient_scope")

    @staticmethod
    def _repository_code(code: str) -> str:
        mapping = {
            "payload_invalid": "invalid_request",
            "payload_too_large": "invalid_request",
            "stale_snapshot": "stale_snapshot",
            "document_write_busy": "document_write_busy",
            "binding_mismatch": "binding_mismatch",
            "idempotency_state_invalid": "backend_error",
        }
        return mapping.get(code, code)
