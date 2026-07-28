"""SQLite persistence and admission queries for Phase 8 Gateway records."""

from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from autocad_contracts import (
    MaterializedTargetRef,
    Phase8CapabilityEvidence,
    canonical_json,
    canonical_phase8_capability_evidence_digest,
    canonical_target_refs_digest,
    validate_bounded_json,
)

from ...phase8_contract_adapter import CompiledProgram
from .database import SqliteDatabase, new_id, utc_now
from .repositories import RepositoryConflict


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_TERMINAL_CONFLICT_STATES = frozenset({"resolved", "abandoned"})


def _json(value: Any, *, limit: int = 512_000) -> str:
    validate_bounded_json(value)
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > limit:
        raise RepositoryConflict("phase8_payload_too_large")
    return encoded


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RepositoryConflict(f"{field}_invalid")
    return value


def _token(value: Any, field: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise RepositoryConflict(f"{field}_invalid")
    return value


def _positive_revision(value: Any, field: str = "revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RepositoryConflict(f"{field}_invalid")
    return value


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise RepositoryConflict("timestamp_invalid") from error
    if parsed.tzinfo is None:
        raise RepositoryConflict("timestamp_invalid")
    return parsed


class Phase8Repository:
    """Owner-scoped, append-only Phase 8 persistence."""

    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    async def create_revision(
        self,
        *,
        owner_subject: str,
        program_id: str,
        revision: int,
        device_id: str,
        document_id: str,
        source_snapshot_id: str,
        expected_document_revision: str,
        source: dict[str, Any],
        source_digest: str,
        semantic_digest: str,
        lineage_kind: str,
        parent_revision: int | None = None,
        base_revision: int | None = None,
        lineage_request_digest: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        _token(owner_subject, "owner_subject")
        _token(program_id, "program_id")
        _token(device_id, "device_id")
        _token(document_id, "document_id")
        _token(source_snapshot_id, "source_snapshot_id")
        revision = _positive_revision(revision)
        source_digest = _digest(source_digest, "source_digest")
        semantic_digest = _digest(semantic_digest, "semantic_digest")
        if not isinstance(source, dict) or source.get("schema_version") != "cad.program/1.0":
            raise RepositoryConflict("source_contract_invalid")
        source_json = _json(source)
        if lineage_kind not in {"root", "patch", "rebase", "conflict_resolution"}:
            raise RepositoryConflict("lineage_kind_invalid")
        if lineage_kind == "root":
            if revision != 1 or parent_revision is not None or base_revision is not None:
                raise RepositoryConflict("lineage_invalid")
        else:
            parent_revision = _positive_revision(parent_revision, "parent_revision")
            if lineage_kind == "rebase":
                base_revision = _positive_revision(base_revision, "base_revision")
            elif base_revision is not None:
                base_revision = _positive_revision(base_revision, "base_revision")
            lineage_request_digest = _digest(
                lineage_request_digest, "lineage_request_digest"
            )

        now = utc_now()
        with self.database.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM phase8_program_revisions "
                "WHERE owner_subject = ? AND source_digest = ?",
                (owner_subject, source_digest),
            ).fetchone()
            if existing is not None:
                value = self._revision(existing)
                requested = {
                    "program_id": program_id,
                    "revision": revision,
                    "owner_subject": owner_subject,
                    "device_id": device_id,
                    "document_id": document_id,
                    "source_snapshot_id": source_snapshot_id,
                    "expected_document_revision": expected_document_revision,
                    "schema_version": "cad.program/1.0",
                    "source_digest": source_digest,
                    "semantic_digest": semantic_digest,
                    "source": deepcopy(source),
                    "lineage_kind": lineage_kind,
                    "parent_revision": parent_revision,
                    "base_revision": base_revision,
                    "lineage_request_digest": lineage_request_digest,
                }
                if all(value[key] == expected for key, expected in requested.items()):
                    return value, True
                raise RepositoryConflict("program_revision_conflict")

            snapshot = conn.execute(
                "SELECT device_id, document_revision FROM snapshots "
                "WHERE owner_subject = ? AND snapshot_id = ?",
                (owner_subject, source_snapshot_id),
            ).fetchone()
            if (
                snapshot is None
                or str(snapshot["device_id"]) != device_id
                or str(snapshot["document_revision"]) != expected_document_revision
            ):
                raise RepositoryConflict("snapshot_binding_mismatch")

            root = conn.execute(
                "SELECT * FROM cad_programs WHERE program_id = ?", (program_id,)
            ).fetchone()
            if root is None:
                if lineage_kind != "root":
                    raise RepositoryConflict("parent_revision_not_found")
                owned_device = conn.execute(
                    "SELECT 1 FROM devices WHERE owner_subject = ? AND device_id = ?",
                    (owner_subject, device_id),
                ).fetchone()
                if owned_device is None:
                    raise RepositoryConflict("not_found")
                conn.execute(
                    "INSERT INTO cad_programs(program_id, owner_subject, device_id, "
                    "document_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (program_id, owner_subject, device_id, document_id, now, now),
                )
            elif (
                str(root["owner_subject"]) != owner_subject
                or str(root["device_id"]) != device_id
                or str(root["document_id"]) != document_id
            ):
                raise RepositoryConflict("not_found")

            latest = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM phase8_program_revisions "
                "WHERE program_id = ?",
                (program_id,),
            ).fetchone()[0]
            if revision != int(latest) + 1:
                raise RepositoryConflict("revision_sequence_conflict")
            for required_revision in (parent_revision, base_revision):
                if required_revision is None:
                    continue
                parent = conn.execute(
                    "SELECT 1 FROM phase8_program_revisions "
                    "WHERE owner_subject = ? AND program_id = ? AND revision = ?",
                    (owner_subject, program_id, required_revision),
                ).fetchone()
                if parent is None:
                    raise RepositoryConflict("parent_revision_not_found")

            try:
                conn.execute(
                    """
                    INSERT INTO phase8_program_revisions(
                        program_id, revision, owner_subject, device_id, document_id,
                        source_snapshot_id, expected_document_revision, schema_version,
                        source_digest, semantic_digest, source_json, lineage_kind,
                        parent_revision, base_revision, lineage_request_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'cad.program/1.0', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        program_id,
                        revision,
                        owner_subject,
                        device_id,
                        document_id,
                        source_snapshot_id,
                        expected_document_revision,
                        source_digest,
                        semantic_digest,
                        source_json,
                        lineage_kind,
                        parent_revision,
                        base_revision,
                        lineage_request_digest,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryConflict("program_revision_conflict") from error
            row = self._revision_row(conn, owner_subject, program_id, revision)
        return self._revision(row), False

    async def get_revision(
        self, owner_subject: str, program_id: str, revision: int
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = self._revision_row(conn, owner_subject, program_id, revision)
        return self._revision(row) if row is not None else None

    async def require_revision_revisable(
        self, owner_subject: str, program_id: str, revision: int
    ) -> None:
        """Reject lineage changes after this revision started execution."""

        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM phase8_revision_usage_events AS usage "
                "JOIN phase8_execution_plans AS plan ON plan.plan_id = usage.plan_id "
                "WHERE plan.owner_subject = ? AND plan.program_id = ? "
                "AND plan.program_revision = ? "
                "AND usage.state IN ('released', 'dispatched', 'running', "
                "'outcome_unknown', 'terminal') LIMIT 1",
                (owner_subject, program_id, revision),
            ).fetchone()
        if row is not None:
            raise RepositoryConflict("revision_execution_started")

    async def next_revision(self, owner_subject: str, program_id: str) -> int:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT MAX(revision) FROM phase8_program_revisions "
                "WHERE owner_subject = ? AND program_id = ?",
                (owner_subject, program_id),
            ).fetchone()
        if row is None or row[0] is None:
            raise RepositoryConflict("not_found")
        return int(row[0]) + 1

    async def seal_plan(
        self,
        *,
        owner_subject: str,
        program_id: str,
        revision: int,
        compilation: CompiledProgram,
        rollout_policy_digest: str,
        rollout_policy_epoch: int,
        plan_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        plan_id = _token(plan_id or new_id("plan"), "plan_id")
        for field in (
            "source_digest",
            "semantic_digest",
            "plan_digest",
            "expansion_digest",
            "effect_digest",
            "target_set_digest",
            "reference_digest",
            "compiler_hash",
        ):
            _digest(getattr(compilation, field), field)
        rollout_policy_digest = _digest(
            rollout_policy_digest, "rollout_policy_digest"
        )
        if (
            isinstance(rollout_policy_epoch, bool)
            or not isinstance(rollout_policy_epoch, int)
            or rollout_policy_epoch < 1
        ):
            raise RepositoryConflict("rollout_policy_epoch_invalid")
        if compilation.source.get("schema_version") != "cad.program/1.0":
            raise RepositoryConflict("source_contract_invalid")
        if compilation.plan.get("schema_version") != "cad.execution-plan/1":
            raise RepositoryConflict("execution_plan_contract_invalid")
        for count in (
            compilation.create_count,
            compilation.modify_count,
            compilation.erase_count,
        ):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise RepositoryConflict("effect_manifest_invalid")
        if compilation.checkpoint_strategy not in {
            "none",
            "cad.rollback.checkpoint/1",
            "cad.rollback.checkpoint/2",
        }:
            raise RepositoryConflict("checkpoint_strategy_invalid")
        if (
            compilation.checkpoint_strategy == "cad.rollback.checkpoint/1"
            and (compilation.modify_count or compilation.erase_count)
        ):
            raise RepositoryConflict("checkpoint_v1_effect_mismatch")
        if compilation.risk_class not in {"low", "medium", "high", "destructive"}:
            raise RepositoryConflict("risk_class_invalid")
        if (
            not isinstance(compilation.trusted_effect_summary, tuple)
            or not compilation.trusted_effect_summary
            or len(compilation.trusted_effect_summary) > 256
        ):
            raise RepositoryConflict("effect_manifest_invalid")
        self._strict_tokens(compilation.required_capabilities, "required_capability")
        self._strict_tokens(compilation.operation_packs, "operation_pack")
        self._strict_tokens(compilation.validation_profiles, "validation_profile")

        now = utc_now()
        with self.database.transaction() as conn:
            revision_row = self._revision_row(conn, owner_subject, program_id, revision)
            if revision_row is None:
                raise RepositoryConflict("not_found")
            source = self._revision(revision_row)
            if (
                source["source_digest"] != compilation.source_digest
                or source["semantic_digest"] != compilation.semantic_digest
                or source["source"] != compilation.source
            ):
                raise RepositoryConflict("compiler_source_mismatch")
            if self._has_open_conflict(conn, owner_subject, program_id, revision):
                raise RepositoryConflict("rebase_conflict_open")
            existing = conn.execute(
                "SELECT * FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND program_id = ? AND program_revision = ?",
                (owner_subject, program_id, revision),
            ).fetchone()
            if existing is not None:
                value = self._plan(existing)
                if (
                    value["plan_id"] == plan_id
                    and value["plan_digest"] == compilation.plan_digest
                    and value["effect_digest"] == compilation.effect_digest
                    and value["target_set_digest"] == compilation.target_set_digest
                    and value["reference_digest"] == compilation.reference_digest
                    and value["plan"] == compilation.plan
                    and value["effect_manifest"] == compilation.effect_manifest
                    and value["risk_class"] == compilation.risk_class
                    and value["trusted_effect_summary"]
                    == list(compilation.trusted_effect_summary)
                    and value["rollout_policy_digest"] == rollout_policy_digest
                    and value["rollout_policy_epoch"] == rollout_policy_epoch
                ):
                    return value, True
                raise RepositoryConflict("sealed_plan_conflict")
            try:
                conn.execute(
                    """
                    INSERT INTO phase8_execution_plans(
                        plan_id, owner_subject, program_id, program_revision,
                        schema_version, source_digest, semantic_digest, compiler_id,
                        compiler_version, compiler_hash, plan_digest, expansion_digest,
                        effect_digest, target_set_digest, reference_digest,
                        plan_json, effect_manifest_json, trusted_effect_summary_json,
                        risk_class, hard_budgets_json,
                        required_capabilities_json, operation_packs_json,
                        validation_profiles_json, runtime_pins_json,
                        checkpoint_strategy, create_count, modify_count, erase_count,
                        rollout_policy_digest, rollout_policy_epoch, sealed_at
                    ) VALUES (?, ?, ?, ?, 'cad.execution-plan/1',
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        owner_subject,
                        program_id,
                        revision,
                        compilation.source_digest,
                        compilation.semantic_digest,
                        _token(compilation.compiler_id, "compiler_id"),
                        _token(compilation.compiler_version, "compiler_version"),
                        compilation.compiler_hash,
                        compilation.plan_digest,
                        compilation.expansion_digest,
                        compilation.effect_digest,
                        compilation.target_set_digest,
                        compilation.reference_digest,
                        _json(compilation.plan),
                        _json(compilation.effect_manifest),
                        _json(list(compilation.trusted_effect_summary), limit=65_536),
                        compilation.risk_class,
                        _json(compilation.hard_budgets, limit=65_536),
                        _json(sorted(set(compilation.required_capabilities)), limit=65_536),
                        _json(sorted(set(compilation.operation_packs)), limit=65_536),
                        _json(sorted(set(compilation.validation_profiles)), limit=65_536),
                        _json(compilation.runtime_pins, limit=65_536),
                        compilation.checkpoint_strategy,
                        compilation.create_count,
                        compilation.modify_count,
                        compilation.erase_count,
                        rollout_policy_digest,
                        rollout_policy_epoch,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryConflict("sealed_plan_conflict") from error
            row = conn.execute(
                "SELECT * FROM phase8_execution_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return self._plan(row), False

    async def get_plan(
        self, owner_subject: str, plan_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
            if row is None:
                return None
            result = self._plan(row)
            result["invalidations"] = [
                dict(item)
                for item in conn.execute(
                    "SELECT invalidation_id, reason, observed_binding_digest, created_at "
                    "FROM phase8_plan_invalidations WHERE owner_subject = ? AND plan_id = ? "
                    "ORDER BY created_at, invalidation_id",
                    (owner_subject, plan_id),
                ).fetchall()
            ]
        return result

    async def get_plan_for_program(
        self, owner_subject: str, program_id: str, revision: int
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT plan_id FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND program_id = ? "
                "AND program_revision = ?",
                (owner_subject, program_id, revision),
            ).fetchone()
        if row is None:
            return None
        return await self.get_plan(owner_subject, str(row["plan_id"]))

    async def create_preview(
        self,
        *,
        owner_subject: str,
        plan_id: str,
        preview_id: str,
        job_id: str,
        execution_binding: dict[str, Any],
        capability_evidence_ids: list[str],
        expires_at: str,
        idempotency_key: str,
        request_digest: str,
    ) -> tuple[dict[str, Any], bool]:
        _token(owner_subject, "owner_subject")
        _token(plan_id, "plan_id")
        _token(preview_id, "preview_id")
        _token(job_id, "job_id")
        _token(idempotency_key, "idempotency_key")
        request_digest = _digest(request_digest, "request_digest")
        binding_digest = _digest(
            execution_binding.get("execution_binding_digest"),
            "execution_binding_digest",
        )
        _timestamp(expires_at)
        binding_json = _json(execution_binding)
        evidence_json = _json(capability_evidence_ids, limit=65_536)
        now = utc_now()
        with self.database.transaction() as conn:
            plan = conn.execute(
                "SELECT 1 FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
            if plan is None:
                raise RepositoryConflict("not_found")
            existing = conn.execute(
                "SELECT * FROM phase8_previews "
                "WHERE owner_subject = ? AND plan_id = ? AND idempotency_key = ?",
                (owner_subject, plan_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                value = self._preview(existing)
                if (
                    value["preview_id"] == preview_id
                    and value["job_id"] == job_id
                    and value["execution_binding"] == execution_binding
                    and value["capability_evidence_ids"] == capability_evidence_ids
                    and value["expires_at"] == expires_at
                    and value["request_digest"] == request_digest
                ):
                    return value, True
                raise RepositoryConflict("idempotency_conflict")
            conn.execute(
                "INSERT INTO phase8_previews("
                "preview_id, owner_subject, plan_id, job_id, "
                "execution_binding_json, execution_binding_digest, "
                "capability_evidence_json, expires_at, idempotency_key, "
                "request_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    preview_id,
                    owner_subject,
                    plan_id,
                    job_id,
                    binding_json,
                    binding_digest,
                    evidence_json,
                    expires_at,
                    idempotency_key,
                    request_digest,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM phase8_previews WHERE preview_id = ?",
                (preview_id,),
            ).fetchone()
        return self._preview(row), False

    async def get_preview(
        self, owner_subject: str, preview_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_previews "
                "WHERE owner_subject = ? AND preview_id = ?",
                (owner_subject, preview_id),
            ).fetchone()
        return self._preview(row) if row is not None else None

    async def invalidate_plan(
        self,
        *,
        owner_subject: str,
        plan_id: str,
        reason: str,
        observed_binding_digest: str,
    ) -> tuple[dict[str, Any], bool]:
        if reason not in {
            "compiler_changed",
            "registry_changed",
            "runtime_changed",
            "policy_changed",
            "capability_changed",
            "feature_disabled",
        }:
            raise RepositoryConflict("invalidation_reason_invalid")
        observed_binding_digest = _digest(
            observed_binding_digest, "observed_binding_digest"
        )
        now = utc_now()
        with self.database.transaction() as conn:
            plan = conn.execute(
                "SELECT 1 FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
            if plan is None:
                raise RepositoryConflict("not_found")
            existing = conn.execute(
                "SELECT * FROM phase8_plan_invalidations "
                "WHERE plan_id = ? AND reason = ? AND observed_binding_digest = ?",
                (plan_id, reason, observed_binding_digest),
            ).fetchone()
            if existing is not None:
                return dict(existing), True
            invalidation_id = new_id("plan-invalidation")
            conn.execute(
                "INSERT INTO phase8_plan_invalidations("
                "invalidation_id, owner_subject, plan_id, reason, "
                "observed_binding_digest, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    invalidation_id,
                    owner_subject,
                    plan_id,
                    reason,
                    observed_binding_digest,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM phase8_plan_invalidations WHERE invalidation_id = ?",
                (invalidation_id,),
            ).fetchone()
        return dict(row), False

    async def create_materialized_ref(
        self,
        *,
        owner_subject: str,
        plan_id: str,
        materialized_ref_id: str,
        snapshot_id: str,
        device_id: str,
        document_id: str,
        document_revision: str,
        ref_kind: str,
        result_digest: str,
        fingerprint_digest: str,
        target_set_digest: str,
        reference_digest: str,
        materialized: dict[str, Any],
        query_digest: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if ref_kind not in {
            "query_result",
            "snapshot_entity",
            "prior_output",
            "component",
        }:
            raise RepositoryConflict("materialized_ref_kind_invalid")
        supplied_digests = {
            "result_digest": _digest(result_digest, "result_digest"),
            "fingerprint_digest": _digest(
                fingerprint_digest, "fingerprint_digest"
            ),
            "target_set_digest": _digest(
                target_set_digest, "target_set_digest"
            ),
            "reference_digest": _digest(
                reference_digest, "reference_digest"
            ),
        }
        if ref_kind == "query_result":
            query_digest = _digest(query_digest, "query_digest")
        elif query_digest is not None:
            query_digest = _digest(query_digest, "query_digest")
        if set(materialized) != {"schema_version", "target_refs"} or (
            materialized.get("schema_version") != "cad.materialized-ref/1"
            or not isinstance(materialized.get("target_refs"), list)
        ):
            raise RepositoryConflict("materialized_ref_invalid")
        try:
            refs = [
                MaterializedTargetRef.model_validate(item)
                for item in materialized["target_refs"]
            ]
        except (TypeError, ValueError) as error:
            raise RepositoryConflict("materialized_ref_invalid") from error
        if not refs or [item.ref_id for item in refs] != sorted(
            item.ref_id for item in refs
        ):
            raise RepositoryConflict("materialized_ref_invalid")
        if len({item.ref_id for item in refs}) != len(refs):
            raise RepositoryConflict("materialized_ref_invalid")
        ref_values = [item.model_dump(mode="json") for item in refs]
        materialized = {
            "schema_version": "cad.materialized-ref/1",
            "target_refs": ref_values,
        }
        computed = {
            "result_digest": self._domain_digest(
                "cad.materialized-ref.result/1",
                {
                    "ref_kind": ref_kind,
                    "query_digest": query_digest,
                    **materialized,
                },
            ),
            "fingerprint_digest": self._domain_digest(
                "cad.materialized-ref.fingerprints/1",
                {
                    "fingerprints": [
                        {
                            "ref_id": item.ref_id,
                            "fingerprint": item.fingerprint,
                        }
                        for item in refs
                    ]
                },
            ),
            "target_set_digest": canonical_target_refs_digest(refs),
            "reference_digest": self._domain_digest(
                "cad.materialized-ref.references/1",
                {
                    "ref_kind": ref_kind,
                    "references": [
                        {
                            "ref_id": item.ref_id,
                            "entity_id": item.entity_id,
                            "fingerprint": item.fingerprint,
                        }
                        for item in refs
                    ],
                },
            ),
        }
        if supplied_digests != computed:
            raise RepositoryConflict("materialized_ref_digest_mismatch")
        (
            result_digest,
            fingerprint_digest,
            target_set_digest,
            reference_digest,
        ) = (
            computed["result_digest"],
            computed["fingerprint_digest"],
            computed["target_set_digest"],
            computed["reference_digest"],
        )
        encoded = _json(materialized)
        now = utc_now()
        with self.database.transaction() as conn:
            plan = conn.execute(
                "SELECT p.target_set_digest, p.reference_digest, "
                "r.device_id, r.document_id, r.source_snapshot_id, "
                "r.expected_document_revision "
                "FROM phase8_execution_plans p "
                "JOIN phase8_program_revisions r "
                "ON r.program_id = p.program_id AND r.revision = p.program_revision "
                "WHERE p.owner_subject = ? AND p.plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
            snapshot = conn.execute(
                "SELECT device_id, document_revision FROM snapshots "
                "WHERE owner_subject = ? AND snapshot_id = ?",
                (owner_subject, snapshot_id),
            ).fetchone()
            if plan is None or snapshot is None:
                raise RepositoryConflict("not_found")
            if (
                any(item.owner_id != owner_subject for item in refs)
                or any(item.device_id != device_id for item in refs)
                or any(item.document_id != document_id for item in refs)
                or any(item.snapshot_id != snapshot_id for item in refs)
                or any(
                    item.document_revision != document_revision
                    for item in refs
                )
                or
                str(snapshot["device_id"]) != device_id
                or str(snapshot["document_revision"]) != document_revision
                or str(plan["device_id"]) != device_id
                or str(plan["document_id"]) != document_id
                or str(plan["source_snapshot_id"]) != snapshot_id
                or str(plan["expected_document_revision"]) != document_revision
                or str(plan["target_set_digest"]) != target_set_digest
                or str(plan["reference_digest"]) != reference_digest
            ):
                raise RepositoryConflict("snapshot_binding_mismatch")
            existing = conn.execute(
                "SELECT * FROM phase8_materialized_refs "
                "WHERE materialized_ref_id = ? OR "
                "(owner_subject = ? AND plan_id = ? AND result_digest = ?) LIMIT 1",
                (materialized_ref_id, owner_subject, plan_id, result_digest),
            ).fetchone()
            if existing is not None:
                value = self._materialized_ref(existing)
                if (
                    value["owner_subject"] == owner_subject
                    and value["plan_id"] == plan_id
                    and value["snapshot_id"] == snapshot_id
                    and value["result_digest"] == result_digest
                    and value["target_set_digest"] == target_set_digest
                    and value["reference_digest"] == reference_digest
                    and value["materialized"] == materialized
                ):
                    return value, True
                raise RepositoryConflict("materialized_ref_conflict")
            conn.execute(
                """
                INSERT INTO phase8_materialized_refs(
                    materialized_ref_id, owner_subject, plan_id, snapshot_id,
                    device_id, document_id, document_revision, ref_kind,
                    query_digest, result_digest, fingerprint_digest,
                    target_set_digest, reference_digest, materialized_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _token(materialized_ref_id, "materialized_ref_id"),
                    owner_subject,
                    plan_id,
                    snapshot_id,
                    device_id,
                    document_id,
                    document_revision,
                    ref_kind,
                    query_digest,
                    result_digest,
                    fingerprint_digest,
                    target_set_digest,
                    reference_digest,
                    encoded,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM phase8_materialized_refs WHERE materialized_ref_id = ?",
                (materialized_ref_id,),
            ).fetchone()
        return self._materialized_ref(row), False

    async def get_materialized_ref(
        self, owner_subject: str, materialized_ref_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_materialized_refs "
                "WHERE owner_subject = ? AND materialized_ref_id = ?",
                (owner_subject, materialized_ref_id),
            ).fetchone()
        return self._materialized_ref(row) if row is not None else None

    @staticmethod
    def _domain_digest(domain: str, value: dict[str, Any]) -> str:
        encoded = canonical_json(
            {"domain": domain, "value": value}
        ).encode("utf-8")
        from hashlib import sha256

        return "sha256:" + sha256(encoded).hexdigest()

    async def create_conflict_report(
        self,
        *,
        owner_subject: str,
        program_id: str,
        source_revision: int,
        candidate_revision: int,
        request_kind: str,
        old_snapshot_id: str,
        new_snapshot_id: str | None,
        request_digest: str,
        conflicts_digest: str,
        conflicts: list[dict[str, Any]],
        conflict_report_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if request_kind not in {"patch", "rebase"}:
            raise RepositoryConflict("conflict_request_kind_invalid")
        if not conflicts:
            raise RepositoryConflict("conflict_report_empty")
        request_digest = _digest(request_digest, "request_digest")
        conflicts_digest = _digest(conflicts_digest, "conflicts_digest")
        conflict_report_id = _token(
            conflict_report_id or new_id("conflict"), "conflict_report_id"
        )
        now = utc_now()
        with self.database.transaction() as conn:
            for revision in (source_revision, candidate_revision):
                if self._revision_row(conn, owner_subject, program_id, revision) is None:
                    raise RepositoryConflict("not_found")
            existing = conn.execute(
                "SELECT * FROM phase8_conflict_reports WHERE conflict_report_id = ? OR "
                "(owner_subject = ? AND program_id = ? AND candidate_revision = ? "
                "AND request_digest = ?) LIMIT 1",
                (
                    conflict_report_id,
                    owner_subject,
                    program_id,
                    candidate_revision,
                    request_digest,
                ),
            ).fetchone()
            if existing is not None:
                value = self._conflict_report(conn, existing)
                if (
                    value["conflicts_digest"] == conflicts_digest
                    and value["conflicts"] == conflicts
                ):
                    return value, True
                raise RepositoryConflict("conflict_report_conflict")
            conn.execute(
                """
                INSERT INTO phase8_conflict_reports(
                    conflict_report_id, owner_subject, program_id, source_revision,
                    candidate_revision, request_kind, old_snapshot_id, new_snapshot_id,
                    request_digest, conflicts_digest, conflicts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict_report_id,
                    owner_subject,
                    program_id,
                    source_revision,
                    candidate_revision,
                    request_kind,
                    old_snapshot_id,
                    new_snapshot_id,
                    request_digest,
                    conflicts_digest,
                    _json(conflicts),
                    now,
                ),
            )
            event_digest = conflicts_digest
            conn.execute(
                "INSERT INTO phase8_conflict_events("
                "conflict_report_id, sequence, owner_subject, state, "
                "resolution_revision, event_digest, created_at"
                ") VALUES (?, 1, ?, 'open', NULL, ?, ?)",
                (conflict_report_id, owner_subject, event_digest, now),
            )
            row = conn.execute(
                "SELECT * FROM phase8_conflict_reports WHERE conflict_report_id = ?",
                (conflict_report_id,),
            ).fetchone()
        return self._conflict_report_from_row(row, "open", 1, None), False

    async def transition_conflict(
        self,
        *,
        owner_subject: str,
        conflict_report_id: str,
        target: str,
        expected_sequence: int,
        event_digest: str,
        resolution_revision: int | None = None,
    ) -> dict[str, Any]:
        if target not in _TERMINAL_CONFLICT_STATES:
            raise RepositoryConflict("conflict_transition_invalid")
        event_digest = _digest(event_digest, "event_digest")
        with self.database.transaction() as conn:
            report = conn.execute(
                "SELECT * FROM phase8_conflict_reports "
                "WHERE owner_subject = ? AND conflict_report_id = ?",
                (owner_subject, conflict_report_id),
            ).fetchone()
            if report is None:
                raise RepositoryConflict("not_found")
            latest = conn.execute(
                "SELECT * FROM phase8_conflict_events WHERE conflict_report_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (conflict_report_id,),
            ).fetchone()
            if int(latest["sequence"]) != expected_sequence:
                if (
                    str(latest["state"]) == target
                    and latest["event_digest"] == event_digest
                    and latest["resolution_revision"] == resolution_revision
                ):
                    return self._conflict_report(conn, report)
                raise RepositoryConflict("cas_conflict")
            if str(latest["state"]) != "open":
                raise RepositoryConflict("conflict_already_closed")
            if target == "resolved":
                resolution_revision = _positive_revision(
                    resolution_revision, "resolution_revision"
                )
                candidate = int(report["candidate_revision"])
                if resolution_revision <= candidate:
                    raise RepositoryConflict("resolution_revision_invalid")
                revision = self._revision_row(
                    conn,
                    owner_subject,
                    str(report["program_id"]),
                    resolution_revision,
                )
                if (
                    revision is None
                    or str(revision["lineage_kind"]) != "conflict_resolution"
                    or int(revision["parent_revision"]) != candidate
                ):
                    raise RepositoryConflict("resolution_revision_invalid")
            elif resolution_revision is not None:
                raise RepositoryConflict("resolution_revision_invalid")
            sequence = expected_sequence + 1
            conn.execute(
                "INSERT INTO phase8_conflict_events("
                "conflict_report_id, sequence, owner_subject, state, "
                "resolution_revision, event_digest, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    conflict_report_id,
                    sequence,
                    owner_subject,
                    target,
                    resolution_revision,
                    event_digest,
                    utc_now(),
                ),
            )
            result = self._conflict_report(conn, report)
        return result

    async def get_conflict_report(
        self, owner_subject: str, conflict_report_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_conflict_reports "
                "WHERE owner_subject = ? AND conflict_report_id = ?",
                (owner_subject, conflict_report_id),
            ).fetchone()
            if row is None:
                return None
            return self._conflict_report(conn, row)

    async def get_conflict_report_for_revision(
        self, owner_subject: str, program_id: str, candidate_revision: int
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_conflict_reports "
                "WHERE owner_subject = ? AND program_id = ? "
                "AND candidate_revision = ?",
                (owner_subject, program_id, candidate_revision),
            ).fetchone()
            if row is None:
                return None
            return self._conflict_report(conn, row)

    async def record_capability_evidence(
        self, value: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        required = {
            "schema_version",
            "evidence_id",
            "evidence_authority",
            "owner_subject",
            "device_id",
            "capability_key",
            "operation_pack",
            "runtime_id",
            "host_family",
            "entity_type",
            "support_state",
            "package_hash",
            "capability_manifest_hash",
            "operation_registry_hash",
            "package_signature_verified",
            "agent_evidence_digest",
            "host_evidence_digest",
            "cohort",
            "evidence_version",
            "issued_at",
            "valid_until",
            "evidence_digest",
        }
        if set(value) != required:
            raise RepositoryConflict("capability_evidence_invalid")
        owner_subject = _token(value["owner_subject"], "owner_subject")
        wire_value = {
            key: item
            for key, item in value.items()
            if key != "owner_subject"
        }
        supplied_digest = wire_value.pop("evidence_digest")
        wire_value["evidence_digest"] = (
            canonical_phase8_capability_evidence_digest(wire_value)
        )
        if supplied_digest != wire_value["evidence_digest"]:
            raise RepositoryConflict("capability_evidence_invalid")
        try:
            evidence = Phase8CapabilityEvidence.model_validate(wire_value)
        except (TypeError, ValueError) as error:
            code = (
                "capability_evidence_untrusted"
                if value.get("evidence_authority") != "gateway_server"
                or value.get("package_signature_verified") is not True
                else "capability_evidence_invalid"
            )
            raise RepositoryConflict(code) from error
        value = {
            **evidence.model_dump(mode="json"),
            "owner_subject": owner_subject,
        }
        now = utc_now()
        with self.database.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM phase8_capability_evidence WHERE evidence_id = ? OR "
                "evidence_digest = ? LIMIT 1",
                (value["evidence_id"], value["evidence_digest"]),
            ).fetchone()
            if existing is not None:
                parsed = self._capability_evidence(existing)
                expected = {**value, "created_at": parsed["created_at"]}
                if parsed == expected:
                    return parsed, True
                raise RepositoryConflict("capability_evidence_conflict")
            owned = conn.execute(
                "SELECT 1 FROM devices WHERE owner_subject = ? AND device_id = ?",
                (value["owner_subject"], value["device_id"]),
            ).fetchone()
            if owned is None:
                raise RepositoryConflict("not_found")
            columns = [
                key for key in required if key != "schema_version"
            ]
            conn.execute(
                "INSERT INTO phase8_capability_evidence("
                + ", ".join(columns)
                + ", created_at) VALUES ("
                + ", ".join("?" for _ in range(len(columns) + 1))
                + ")",
                tuple(
                    int(value[column])
                    if column == "package_signature_verified"
                    else value[column]
                    for column in columns
                )
                + (now,),
            )
            row = conn.execute(
                "SELECT * FROM phase8_capability_evidence WHERE evidence_id = ?",
                (value["evidence_id"],),
            ).fetchone()
        return self._capability_evidence(row), False

    async def get_capability_evidence(
        self, owner_subject: str, evidence_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_capability_evidence "
                "WHERE owner_subject = ? AND evidence_id = ?",
                (owner_subject, evidence_id),
            ).fetchone()
        return self._capability_evidence(row) if row is not None else None

    async def append_usage_event(
        self,
        *,
        owner_subject: str,
        plan_id: str,
        state: str,
        external_id: str,
        binding_digest: str,
    ) -> tuple[dict[str, Any], bool]:
        if state not in {
            "previewed",
            "intent_created",
            "consent_created",
            "released",
            "dispatched",
            "running",
            "outcome_unknown",
            "terminal",
        }:
            raise RepositoryConflict("usage_state_invalid")
        binding_digest = _digest(binding_digest, "binding_digest")
        _token(external_id, "external_id")
        now = utc_now()
        with self.database.transaction() as conn:
            plan = conn.execute(
                "SELECT 1 FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
            if plan is None:
                raise RepositoryConflict("not_found")
            existing = conn.execute(
                "SELECT * FROM phase8_revision_usage_events "
                "WHERE plan_id = ? AND state = ? AND external_id = ?",
                (plan_id, state, external_id),
            ).fetchone()
            if existing is not None:
                parsed = dict(existing)
                if (
                    parsed["owner_subject"] == owner_subject
                    and parsed["binding_digest"] == binding_digest
                ):
                    return parsed, True
                raise RepositoryConflict("usage_event_conflict")
            usage_event_id = new_id("usage")
            conn.execute(
                "INSERT INTO phase8_revision_usage_events("
                "usage_event_id, owner_subject, plan_id, state, external_id, "
                "binding_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    usage_event_id,
                    owner_subject,
                    plan_id,
                    state,
                    external_id,
                    binding_digest,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM phase8_revision_usage_events WHERE usage_event_id = ?",
                (usage_event_id,),
            ).fetchone()
        return dict(row), False

    async def matching_capability_evidence(
        self,
        *,
        owner_subject: str,
        device_id: str,
        capability_key: str,
        operation_pack: str,
        runtime_id: str,
        host_family: str,
        cohort: str,
        package_hash: str,
        capability_manifest_hash: str,
        operation_registry_hash: str,
        minimum_support_states: tuple[str, ...],
        now: str | None = None,
    ) -> dict[str, Any] | None:
        now = now or utc_now()
        placeholders = ", ".join("?" for _ in minimum_support_states)
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_capability_evidence "
                "WHERE owner_subject = ? AND device_id = ? AND capability_key = ? "
                "AND operation_pack = ? AND runtime_id = ? AND host_family = ? "
                "AND cohort = ? AND package_hash = ? "
                "AND capability_manifest_hash = ? AND operation_registry_hash = ? "
                "AND evidence_authority = 'gateway_server' "
                "AND package_signature_verified = 1 "
                f"AND support_state IN ({placeholders}) AND valid_until > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (
                    owner_subject,
                    device_id,
                    capability_key,
                    operation_pack,
                    runtime_id,
                    host_family,
                    cohort,
                    package_hash,
                    capability_manifest_hash,
                    operation_registry_hash,
                    *minimum_support_states,
                    now,
                ),
            ).fetchone()
        return self._capability_evidence(row) if row is not None else None

    async def bind_intent(
        self,
        *,
        owner_subject: str,
        intent_id: str,
        plan_id: str,
        binding_digest: str,
    ) -> tuple[dict[str, Any], bool]:
        binding_digest = _digest(binding_digest, "binding_digest")
        now = utc_now()
        with self.database.transaction() as conn:
            intent = conn.execute(
                "SELECT program_id, program_revision, program_digest, risk_class, "
                "trusted_effect_summary_json, state, state_version "
                "FROM execution_intents WHERE owner_subject = ? AND intent_id = ?",
                (owner_subject, intent_id),
            ).fetchone()
            plan = conn.execute(
                "SELECT * FROM phase8_execution_plans "
                "WHERE owner_subject = ? AND plan_id = ?",
                (owner_subject, plan_id),
            ).fetchone()
            if intent is None or plan is None:
                raise RepositoryConflict("not_found")
            consent_exists = conn.execute(
                "SELECT 1 FROM consents WHERE owner_subject = ? AND intent_id = ?",
                (owner_subject, intent_id),
            ).fetchone()
            if (
                str(intent["state"]) not in {"awaiting_approval", "ready"}
                or int(intent["state_version"]) != 0
                or consent_exists is not None
                or str(intent["program_id"]) != str(plan["program_id"])
                or int(intent["program_revision"]) != int(plan["program_revision"])
                or str(intent["program_digest"]) != str(plan["source_digest"])
                or str(intent["risk_class"]) != str(plan["risk_class"])
                or json.loads(intent["trusted_effect_summary_json"])
                != json.loads(plan["trusted_effect_summary_json"])
            ):
                raise RepositoryConflict("intent_plan_binding_mismatch")
            values = (
                intent_id,
                owner_subject,
                plan_id,
                plan["source_digest"],
                plan["semantic_digest"],
                plan["plan_digest"],
                plan["expansion_digest"],
                plan["effect_digest"],
                plan["target_set_digest"],
                plan["reference_digest"],
                plan["compiler_hash"],
                plan["risk_class"],
                plan["trusted_effect_summary_json"],
                plan["rollout_policy_digest"],
                plan["rollout_policy_epoch"],
                binding_digest,
                now,
            )
            existing = conn.execute(
                "SELECT * FROM phase8_intent_bindings WHERE intent_id = ? OR "
                "binding_digest = ? LIMIT 1",
                (intent_id, binding_digest),
            ).fetchone()
            if existing is not None:
                parsed = dict(existing)
                if all(
                    parsed[key] == expected
                    for key, expected in zip(
                        (
                            "intent_id",
                            "owner_subject",
                            "plan_id",
                            "source_digest",
                            "semantic_digest",
                            "plan_digest",
                            "expansion_digest",
                            "effect_digest",
                            "target_set_digest",
                            "reference_digest",
                            "compiler_hash",
                            "risk_class",
                            "trusted_effect_summary_json",
                            "rollout_policy_digest",
                            "rollout_policy_epoch",
                            "binding_digest",
                        ),
                        values[:-1],
                    )
                ):
                    return parsed, True
                raise RepositoryConflict("intent_plan_binding_conflict")
            conn.execute(
                """
                INSERT INTO phase8_intent_bindings(
                    intent_id, owner_subject, plan_id, source_digest, semantic_digest,
                    plan_digest, expansion_digest, effect_digest, target_set_digest,
                    reference_digest, compiler_hash, risk_class,
                    trusted_effect_summary_json, rollout_policy_digest,
                    rollout_policy_epoch, binding_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            row = conn.execute(
                "SELECT * FROM phase8_intent_bindings WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return dict(row), False

    async def get_intent_binding(
        self, owner_subject: str, intent_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_intent_bindings "
                "WHERE owner_subject = ? AND intent_id = ?",
                (owner_subject, intent_id),
            ).fetchone()
        return dict(row) if row is not None else None

    async def bind_consent(
        self,
        *,
        owner_subject: str,
        consent_id: str,
        intent_id: str,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self.database.transaction() as conn:
            consent = conn.execute(
                "SELECT intent_id, state, state_version FROM consents "
                "WHERE owner_subject = ? AND consent_id = ?",
                (owner_subject, consent_id),
            ).fetchone()
            binding = conn.execute(
                "SELECT * FROM phase8_intent_bindings "
                "WHERE owner_subject = ? AND intent_id = ?",
                (owner_subject, intent_id),
            ).fetchone()
            if consent is None or binding is None:
                raise RepositoryConflict("not_found")
            if (
                str(consent["intent_id"]) != intent_id
                or str(consent["state"]) != "requested"
                or int(consent["state_version"]) != 0
            ):
                raise RepositoryConflict("consent_binding_mismatch")
            existing = conn.execute(
                "SELECT * FROM phase8_consent_bindings WHERE consent_id = ?",
                (consent_id,),
            ).fetchone()
            if existing is not None:
                parsed = dict(existing)
                if (
                    parsed["owner_subject"] == owner_subject
                    and parsed["intent_id"] == intent_id
                    and parsed["binding_digest"] == binding["binding_digest"]
                ):
                    return parsed, True
                raise RepositoryConflict("consent_binding_conflict")
            conn.execute(
                "INSERT INTO phase8_consent_bindings("
                "consent_id, owner_subject, intent_id, binding_digest, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    consent_id,
                    owner_subject,
                    intent_id,
                    binding["binding_digest"],
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM phase8_consent_bindings WHERE consent_id = ?",
                (consent_id,),
            ).fetchone()
        return dict(row), False

    async def get_consent_binding(
        self, owner_subject: str, consent_id: str
    ) -> dict[str, Any] | None:
        with self.database.read_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase8_consent_bindings "
                "WHERE owner_subject = ? AND consent_id = ?",
                (owner_subject, consent_id),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _strict_tokens(values: tuple[str, ...], field: str) -> None:
        if not isinstance(values, tuple) or len(values) > 256 or len(set(values)) != len(values):
            raise RepositoryConflict(f"{field}_invalid")
        for value in values:
            _token(value, field)

    @staticmethod
    def _revision_row(
        conn: Any, owner_subject: str, program_id: str, revision: int
    ) -> Any | None:
        return conn.execute(
            "SELECT * FROM phase8_program_revisions "
            "WHERE owner_subject = ? AND program_id = ? AND revision = ?",
            (owner_subject, program_id, revision),
        ).fetchone()

    @staticmethod
    def _revision(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["revision"] = int(value["revision"])
        value["parent_revision"] = (
            int(value["parent_revision"]) if value["parent_revision"] is not None else None
        )
        value["base_revision"] = (
            int(value["base_revision"]) if value["base_revision"] is not None else None
        )
        value["source"] = json.loads(value.pop("source_json"))
        return value

    @staticmethod
    def _plan(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["program_revision"] = int(value["program_revision"])
        for field in ("create_count", "modify_count", "erase_count"):
            value[field] = int(value[field])
        value["rollout_policy_epoch"] = int(value["rollout_policy_epoch"])
        for source, target in (
            ("plan_json", "plan"),
            ("effect_manifest_json", "effect_manifest"),
            ("trusted_effect_summary_json", "trusted_effect_summary"),
            ("hard_budgets_json", "hard_budgets"),
            ("required_capabilities_json", "required_capabilities"),
            ("operation_packs_json", "operation_packs"),
            ("validation_profiles_json", "validation_profiles"),
            ("runtime_pins_json", "runtime_pins"),
        ):
            value[target] = json.loads(value.pop(source))
        return value

    @staticmethod
    def _materialized_ref(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["materialized"] = json.loads(value.pop("materialized_json"))
        return value

    @staticmethod
    def _has_open_conflict(
        conn: Any, owner_subject: str, program_id: str, revision: int
    ) -> bool:
        row = conn.execute(
            """
            SELECT e.state
            FROM phase8_conflict_reports r
            JOIN phase8_conflict_events e
              ON e.conflict_report_id = r.conflict_report_id
            WHERE r.owner_subject = ? AND r.program_id = ?
              AND r.candidate_revision = ?
              AND e.sequence = (
                  SELECT MAX(e2.sequence) FROM phase8_conflict_events e2
                  WHERE e2.conflict_report_id = r.conflict_report_id
              )
            LIMIT 1
            """,
            (owner_subject, program_id, revision),
        ).fetchone()
        return row is not None and str(row["state"]) == "open"

    @classmethod
    def _conflict_report(cls, conn: Any, row: Any) -> dict[str, Any]:
        event = conn.execute(
            "SELECT * FROM phase8_conflict_events WHERE conflict_report_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (row["conflict_report_id"],),
        ).fetchone()
        return cls._conflict_report_from_row(
            row,
            str(event["state"]),
            int(event["sequence"]),
            (
                int(event["resolution_revision"])
                if event["resolution_revision"] is not None
                else None
            ),
        )

    @staticmethod
    def _conflict_report_from_row(
        row: Any, state: str, sequence: int, resolution_revision: int | None
    ) -> dict[str, Any]:
        value = dict(row)
        value["source_revision"] = int(value["source_revision"])
        value["candidate_revision"] = int(value["candidate_revision"])
        value["conflicts"] = json.loads(value.pop("conflicts_json"))
        value["state"] = state
        value["state_sequence"] = sequence
        value["resolution_revision"] = resolution_revision
        return value

    @staticmethod
    def _capability_evidence(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["schema_version"] = "cad.capability-evidence/1"
        value["package_signature_verified"] = bool(
            value["package_signature_verified"]
        )
        return value

    @staticmethod
    def _preview(row: Any) -> dict[str, Any]:
        value = dict(row)
        value["execution_binding"] = json.loads(
            value.pop("execution_binding_json")
        )
        value["capability_evidence_ids"] = json.loads(
            value.pop("capability_evidence_json")
        )
        return value
