"""Backend-neutral lifecycle for previewable, explicitly approved dimension plans."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping


class DimensionPlanError(ValueError):
    """Base error for invalid plan requests."""


class DimensionPlanNotFoundError(DimensionPlanError):
    """Raised when a plan ID is not present in the current server process."""


class DimensionPlanConflictError(DimensionPlanError):
    """Raised when a caller tries to revise a stale or non-draft plan."""


def _json_value(value: Any, field_name: str) -> Any:
    """Copy through JSON so every public DTO is safe to return from an MCP tool."""

    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise DimensionPlanError(f"{field_name} must contain JSON-compatible values") from exc


@dataclass(frozen=True)
class PlannedDimension:
    """A proposed dimension with a stable user-facing D-number."""

    dimension_id: str
    kind: str
    geometry: dict[str, Any]
    placement: dict[str, Any]
    text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.dimension_id

    @property
    def entity_id(self) -> Any:
        return self.geometry.get("entity_id")

    @property
    def p1(self) -> Any:
        return self.geometry.get("p1")

    @property
    def p2(self) -> Any:
        return self.geometry.get("p2")

    @property
    def base(self) -> Any:
        return self.placement.get("base")

    @property
    def angle(self) -> Any:
        return self.placement.get("angle")

    @property
    def preview_label(self) -> dict[str, Any]:
        anchor = self.placement.get("label_anchor")
        if anchor is None:
            anchor = self.placement.get("base") or self.placement.get("point")
        return {"text": self.dimension_id, "anchor": deepcopy(anchor)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_id": self.dimension_id,
            "label": self.label,
            "kind": self.kind,
            "entity_id": deepcopy(self.entity_id),
            "p1": deepcopy(self.p1),
            "p2": deepcopy(self.p2),
            "base": deepcopy(self.base),
            "angle": deepcopy(self.angle),
            "geometry": deepcopy(self.geometry),
            "placement": deepcopy(self.placement),
            "text": self.text,
            "metadata": deepcopy(self.metadata),
            "preview_label": self.preview_label,
        }


@dataclass(frozen=True)
class DimensionPlan:
    """Snapshot returned to preview clients and passed whole to a commit executor."""

    plan_id: str
    profile_name: str
    target: dict[str, Any]
    dimensions: tuple[PlannedDimension, ...]
    created_at: str
    revision: int = 1
    status: str = "draft"
    next_dimension_number: int = 1
    commit_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "profile_name": self.profile_name,
            "target": deepcopy(self.target),
            "created_at": self.created_at,
            "revision": self.revision,
            "status": self.status,
            "dimension_count": len(self.dimensions),
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
            "preview_labels": [dimension.preview_label for dimension in self.dimensions],
            "commit_result": deepcopy(self.commit_result),
            "undo_group_required": True,
        }


CommitExecutor = Callable[
    [DimensionPlan],
    Mapping[str, Any] | None | Awaitable[Mapping[str, Any] | None],
]


class DimensionPlanStore:
    """In-process plan store with revision checks and idempotent serialized commit."""

    def __init__(self, *, max_plans: int = 128, max_dimensions: int = 512) -> None:
        if max_plans <= 0 or max_dimensions <= 0:
            raise ValueError("Dimension plan limits must be greater than zero")
        self._plans: dict[str, DimensionPlan] = {}
        self._commit_lock = asyncio.Lock()
        self._max_plans = max_plans
        self._max_dimensions = max_dimensions

    def create(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        profile_name: str,
        target: Mapping[str, Any] | None = None,
    ) -> DimensionPlan:
        if len(self._plans) >= self._max_plans:
            evictable = next(
                (plan_id for plan_id, plan in self._plans.items() if plan.status == "committed"),
                None,
            )
            if evictable is None:
                raise DimensionPlanError(
                    "All in-process dimension plan slots contain active drafts; "
                    "commit or discard a draft before creating another"
                )
            del self._plans[evictable]
        candidate_list = list(candidates)
        if len(candidate_list) > self._max_dimensions:
            raise DimensionPlanError(
                f"A dimension plan cannot exceed {self._max_dimensions} dimensions"
            )
        dimensions: list[PlannedDimension] = []
        next_number = 1
        for candidate in candidate_list:
            dimensions.append(self._make_dimension(candidate, next_number))
            next_number += 1
        if not dimensions:
            raise DimensionPlanError("A dimension plan must contain at least one dimension")

        plan = DimensionPlan(
            plan_id=f"dplan_{uuid.uuid4().hex}",
            profile_name=str(profile_name).strip(),
            target=_json_value(dict(target or {}), "target"),
            dimensions=tuple(dimensions),
            created_at=datetime.now(timezone.utc).isoformat(),
            next_dimension_number=next_number,
        )
        if not plan.profile_name:
            raise DimensionPlanError("profile_name cannot be empty")
        self._plans[plan.plan_id] = plan
        return plan

    def plan_ids(self) -> frozenset[str]:
        return frozenset(self._plans)

    def get(self, plan_id: str) -> DimensionPlan:
        try:
            return self._plans[plan_id]
        except KeyError as exc:
            raise DimensionPlanNotFoundError(f"Unknown dimension plan: {plan_id}") from exc

    def revise(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        remove_ids: Iterable[str] = (),
        placement_overrides: Mapping[str, Mapping[str, Any]] | None = None,
        add_candidates: Iterable[Mapping[str, Any]] = (),
    ) -> DimensionPlan:
        plan = self.get(plan_id)
        self._ensure_editable(plan, expected_revision)

        remove = set(remove_ids)
        overrides = placement_overrides or {}
        known_ids = {dimension.dimension_id for dimension in plan.dimensions}
        unknown_ids = (remove | set(overrides)) - known_ids
        if unknown_ids:
            unknown = ", ".join(sorted(unknown_ids))
            raise DimensionPlanError(f"Unknown dimension IDs: {unknown}")

        revised: list[PlannedDimension] = []
        for dimension in plan.dimensions:
            if dimension.dimension_id in remove:
                continue
            if dimension.dimension_id in overrides:
                placement = deepcopy(dimension.placement)
                placement.update(
                    _json_value(
                        dict(overrides[dimension.dimension_id]),
                        f"placement override for {dimension.dimension_id}",
                    )
                )
                dimension = replace(dimension, placement=placement)
            revised.append(dimension)

        next_number = plan.next_dimension_number
        additions = list(add_candidates)
        if len(revised) + len(additions) > self._max_dimensions:
            raise DimensionPlanError(
                f"A dimension plan cannot exceed {self._max_dimensions} dimensions"
            )
        for candidate in additions:
            revised.append(self._make_dimension(candidate, next_number))
            next_number += 1
        if not revised:
            raise DimensionPlanError("A dimension plan must retain at least one dimension")

        updated = replace(
            plan,
            dimensions=tuple(revised),
            revision=plan.revision + 1,
            next_dimension_number=next_number,
        )
        self._plans[plan_id] = updated
        return updated

    async def commit(
        self,
        plan_id: str,
        executor: CommitExecutor,
        *,
        expected_revision: int | None = None,
    ) -> DimensionPlan:
        """Run one whole-plan executor once; duplicate commits return the first result."""

        async with self._commit_lock:
            plan = self.get(plan_id)
            if expected_revision is not None and plan.revision != expected_revision:
                raise DimensionPlanConflictError(
                    f"Plan revision is {plan.revision}, not expected revision {expected_revision}"
                )
            if plan.status == "committed":
                return plan
            if plan.status != "draft":
                raise DimensionPlanConflictError(
                    f"Dimension plan {plan_id} is currently {plan.status}"
                )

            committing = replace(plan, status="committing")
            self._plans[plan_id] = committing
            try:
                result = executor(committing)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None and not isinstance(result, Mapping):
                    raise DimensionPlanError("commit executor result must be an object")
                commit_result = _json_value(dict(result or {}), "commit executor result")
            except BaseException:
                self._plans[plan_id] = replace(committing, status="draft")
                raise

            committed = replace(
                committing,
                status="committed",
                commit_result=commit_result,
            )
            self._plans[plan_id] = committed
            return committed

    def discard(self, plan_id: str) -> bool:
        plan = self.get(plan_id)
        if plan.status != "draft":
            raise DimensionPlanConflictError(
                f"Only a draft dimension plan can be discarded; current status is {plan.status}"
            )
        del self._plans[plan_id]
        return True

    @staticmethod
    def _make_dimension(candidate: Mapping[str, Any], number: int) -> PlannedDimension:
        kind = str(candidate.get("kind", "")).strip().lower()
        if not kind:
            raise DimensionPlanError("Each planned dimension requires a kind")
        geometry_raw = candidate.get("geometry", {})
        if not isinstance(geometry_raw, Mapping):
            raise DimensionPlanError("geometry must be an object")
        geometry = _json_value(dict(geometry_raw), "geometry")
        for field_name in ("entity_id", "p1", "p2", "center", "point"):
            if field_name in candidate:
                geometry[field_name] = _json_value(candidate[field_name], field_name)
        if not geometry:
            raise DimensionPlanError("Each planned dimension requires non-empty geometry")
        placement_raw = candidate.get("placement", {})
        if not isinstance(placement_raw, Mapping):
            raise DimensionPlanError("placement must be an object")
        placement = _json_value(dict(placement_raw), "placement")
        for field_name in ("base", "angle", "label_anchor"):
            if field_name in candidate:
                placement[field_name] = _json_value(candidate[field_name], field_name)
        metadata = candidate.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise DimensionPlanError("metadata must be an object")
        text = candidate.get("text")
        return PlannedDimension(
            dimension_id=f"D{number}",
            kind=kind,
            geometry=geometry,
            placement=placement,
            text=None if text is None else str(text),
            metadata=_json_value(dict(metadata), "metadata"),
        )

    @staticmethod
    def _ensure_editable(plan: DimensionPlan, expected_revision: int) -> None:
        if plan.status != "draft":
            raise DimensionPlanConflictError(
                f"Only a draft dimension plan can be revised; current status is {plan.status}"
            )
        if plan.revision != expected_revision:
            raise DimensionPlanConflictError(
                f"Plan revision is {plan.revision}, not expected revision {expected_revision}"
            )
