"""Deterministic administrator diagnostics for publication and Solver readiness."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.domain.models import AdminRecipeCatalogItem, MealSlot
from mealpilot.nutrition.catalog import FoodNutrition


ResolutionKind = Literal[
    "RUN_LLM_STRUCTURE",
    "ADMIN_STRUCTURE_REVIEW",
    "TRUSTED_CANONICAL_MAPPING",
    "TRUSTED_QUANTITY_REVIEW",
    "TRUSTED_ALLERGEN_REVIEW",
    "IMPORT_FORMAL_NUTRITION",
    "NUTRITION_BASIS_REVIEW",
]


class CorpusRemediationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str
    recipe_id: str | None = None
    title: str
    scope: Literal["PUBLICATION", "SOLVER"]
    reason_code: str
    resolution_kind: ResolutionKind
    canonical_ids: list[str] = Field(default_factory=list)
    llm_assist_allowed: bool
    requires_human_review: Literal[True] = True


class SolverReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    published_recipe_count: int = Field(ge=0)
    solver_ready_count: int = Field(ge=0)
    published_display_only_count: int = Field(ge=0)
    pending_review_count: int = Field(ge=0)
    publishable_review_count: int = Field(ge=0)
    publication_blocked_count: int = Field(ge=0)
    per_slot_solver_ready_count: dict[MealSlot, int]
    strict_planning_ready: bool
    formal_nutrition_record_count: int = Field(ge=0)
    formal_nutrition_data_versions: list[str]
    missing_formal_nutrition_ids: list[str]
    blocker_counts: dict[str, int]
    actions: list[CorpusRemediationAction]


def _resolution(reason_code: str, scope: Literal["PUBLICATION", "SOLVER"]) -> tuple[ResolutionKind, bool]:
    if scope == "PUBLICATION":
        if reason_code in {"SERVINGS_MISSING", "TIME_MISSING", "MEAL_SLOTS_MISSING"}:
            return "RUN_LLM_STRUCTURE", True
        if reason_code.startswith(("NON_ACTIONABLE_STEP:", "IMAGE_DEPENDENT_STEP:", "MEDICAL_STEP_TEXT:")):
            return "ADMIN_STRUCTURE_REVIEW", True
        if reason_code == "INGREDIENT_MAPPING_INCOMPLETE":
            return "TRUSTED_CANONICAL_MAPPING", False
        if reason_code in {"INGREDIENT_QUANTITY_INCOMPLETE", "INVALID_NUTRITION_EXCLUSION"}:
            return "TRUSTED_QUANTITY_REVIEW", False
        return "ADMIN_STRUCTURE_REVIEW", False

    if reason_code == "ALLERGEN_COMPOSITION_INCOMPLETE":
        return "TRUSTED_ALLERGEN_REVIEW", False
    if reason_code == "NUTRITION_COVERAGE_INCOMPLETE":
        return "IMPORT_FORMAL_NUTRITION", False
    if reason_code == "NUTRITION_QUANTITY_INCOMPLETE":
        return "TRUSTED_QUANTITY_REVIEW", False
    return "NUTRITION_BASIS_REVIEW", False


def _canonical_ids_for_reason(
    item: AdminRecipeCatalogItem,
    reason_code: str,
    formal_ids: set[str],
) -> list[str]:
    included = [ingredient for ingredient in item.ingredients if ingredient.nutrition_calculation_role == "INCLUDED"]
    if reason_code == "NUTRITION_COVERAGE_INCOMPLETE":
        return sorted({ingredient.canonical_id for ingredient in included if ingredient.canonical_id not in formal_ids})
    if reason_code in {"NUTRITION_QUANTITY_INCOMPLETE", "INGREDIENT_QUANTITY_INCOMPLETE"}:
        return sorted({ingredient.canonical_id for ingredient in included if ingredient.amount_g is None})
    if reason_code == "ALLERGEN_COMPOSITION_INCOMPLETE":
        return sorted({ingredient.canonical_id for ingredient in item.ingredients if not ingredient.allergen_composition_known})
    return []


def build_solver_readiness_report(
    items: list[AdminRecipeCatalogItem],
    foods: list[FoodNutrition],
) -> SolverReadinessReport:
    """Convert raw blocker codes into an ordered, evidence-preserving remediation queue."""

    active = [item for item in items if item.origin == "ACTIVE_CATALOG"]
    reviews = [item for item in items if item.origin == "REVIEW_QUEUE" and item.lifecycle_status == "PENDING"]
    solver_ready = [item for item in active if item.solver_eligible]
    display_only = [item for item in active if not item.solver_eligible]
    publishable = [
        item for item in reviews
        if item.processing_stage == "FINAL_VALIDATED"
        and item.quality_status in {"PUBLICATION_READY", "SOLVER_READY"}
        and not item.blocking_reasons
    ]
    publication_blocked = [item for item in reviews if item.blocking_reasons]

    per_slot = {slot: sum(slot in item.supported_slots for item in solver_ready) for slot in MealSlot}
    strict_ready = bool(solver_ready) and all(per_slot[slot] > 0 for slot in MealSlot)
    formal_ids = {food.canonical_id for food in foods}
    versions = sorted({food.source.data_version for food in foods})

    blocker_counts: Counter[str] = Counter()
    missing_formal_ids: set[str] = set()
    actions: list[CorpusRemediationAction] = []

    for item in sorted(items, key=lambda value: (value.origin, value.title, value.record_id)):
        if item.origin == "REVIEW_QUEUE" and item.lifecycle_status == "PENDING":
            if item.processing_stage in {"INITIAL_VALIDATED", "LLM_FAILED"}:
                blocker_counts["LLM_STRUCTURE_PENDING"] += 1
                actions.append(CorpusRemediationAction(
                    record_id=item.record_id,
                    recipe_id=item.recipe_id,
                    title=item.title,
                    scope="PUBLICATION",
                    reason_code="LLM_STRUCTURE_PENDING",
                    resolution_kind="RUN_LLM_STRUCTURE",
                    canonical_ids=[],
                    llm_assist_allowed=True,
                ))
            for reason in item.blocking_reasons:
                blocker_counts[reason] += 1
                resolution, llm_allowed = _resolution(reason, "PUBLICATION")
                actions.append(CorpusRemediationAction(
                    record_id=item.record_id,
                    recipe_id=item.recipe_id,
                    title=item.title,
                    scope="PUBLICATION",
                    reason_code=reason,
                    resolution_kind=resolution,
                    canonical_ids=_canonical_ids_for_reason(item, reason, formal_ids),
                    llm_assist_allowed=llm_allowed,
                ))

        publication_reasons = set(item.blocking_reasons)
        solver_reasons = [reason for reason in item.solver_blocking_reasons if reason not in publication_reasons]
        # These two are aggregate/derived blockers. Prefer the actionable root cause when one exists.
        actionable_solver_reasons = [reason for reason in solver_reasons if reason not in {"NUTRITION_NOT_CALCULABLE", "RECIPE_NOT_SOLVER_ELIGIBLE"}]
        if not actionable_solver_reasons and solver_reasons:
            actionable_solver_reasons = solver_reasons[:1]

        for reason in actionable_solver_reasons:
            blocker_counts[reason] += 1
            canonical_ids = _canonical_ids_for_reason(item, reason, formal_ids)
            if reason == "NUTRITION_COVERAGE_INCOMPLETE":
                missing_formal_ids.update(canonical_ids)
            resolution, llm_allowed = _resolution(reason, "SOLVER")
            actions.append(CorpusRemediationAction(
                record_id=item.record_id,
                recipe_id=item.recipe_id,
                title=item.title,
                scope="SOLVER",
                reason_code=reason,
                resolution_kind=resolution,
                canonical_ids=canonical_ids,
                llm_assist_allowed=llm_allowed,
            ))

    return SolverReadinessReport(
        published_recipe_count=len(active),
        solver_ready_count=len(solver_ready),
        published_display_only_count=len(display_only),
        pending_review_count=len(reviews),
        publishable_review_count=len(publishable),
        publication_blocked_count=len(publication_blocked),
        per_slot_solver_ready_count=per_slot,
        strict_planning_ready=strict_ready,
        formal_nutrition_record_count=len(foods),
        formal_nutrition_data_versions=versions,
        missing_formal_nutrition_ids=sorted(missing_formal_ids),
        blocker_counts=dict(sorted(blocker_counts.items())),
        actions=actions,
    )
