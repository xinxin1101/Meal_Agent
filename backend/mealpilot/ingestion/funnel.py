"""Read-only recomputation and funnel metrics for the real recipe review corpus."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.ingestion.quality import QUALITY_GATE_VERSION, build_quality_draft
from mealpilot.ingestion.review import RecipeReviewItem
from mealpilot.nutrition.catalog import FoodNutrition


REPORT_VERSION = "m40-f-v1"
PriorityCategory = Literal[
    "servings",
    "prep_time",
    "meal_slots",
    "cooking_steps",
    "ingredient_identity",
    "ingredient_quantity",
    "allergen_composition",
    "nutrition_coverage",
    "other",
]
RemediationKind = Literal["DETERMINISTIC_OR_LLM_ASSIST", "TRUSTED_DATA_ENRICHMENT", "HUMAN_REVIEW"]
FunnelStage = Literal["PUBLICATION", "SOLVER"]


class FunnelBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    count: int = Field(ge=1)
    affected_rate: Decimal = Field(ge=0, le=1)
    sample_review_ids: list[str] = Field(default_factory=list, max_length=5)


class FunnelPriority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: PriorityCategory
    stage: FunnelStage
    count: int = Field(ge=1)
    affected_rate: Decimal = Field(ge=0, le=1)
    reason_codes: list[str] = Field(min_length=1)
    remediation: RemediationKind


class FunnelDriftRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    source_id: str
    stored_quality_gate_version: str
    stored_status: str
    recomputed_status: str


class FunnelRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    source_id: str
    title: str
    lifecycle_status: str
    processing_stage: str
    stored_quality_gate_version: str
    stored_status: str
    recomputed_status: Literal["BLOCKED", "PUBLICATION_READY", "SOLVER_READY"]
    publication_blocking_reasons: list[str]
    solver_blocking_reasons: list[str]
    unspecified_quantity_names: list[str]


class RecipeCorpusFunnelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_version: Literal["m40-f-v1"] = REPORT_VERSION
    generated_at: datetime
    quality_gate_version: str
    total_reviews: int = Field(ge=0)
    lifecycle_counts: dict[str, int]
    stored_gate_version_counts: dict[str, int]
    recomputed_status_counts: dict[str, int]
    blocked_count: int = Field(ge=0)
    publication_ready_count: int = Field(ge=0)
    solver_ready_count: int = Field(ge=0)
    publishable_count: int = Field(ge=0)
    publishable_rate: Decimal = Field(ge=0, le=1)
    solver_ready_rate: Decimal = Field(ge=0, le=1)
    solver_within_publishable_rate: Decimal = Field(ge=0, le=1)
    publication_blockers: list[FunnelBlocker]
    solver_blockers: list[FunnelBlocker]
    priorities: list[FunnelPriority]
    drift_count: int = Field(ge=0)
    drift_records: list[FunnelDriftRecord]
    records: list[FunnelRecord]


def _rate(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _rank_blockers(
    affected_reviews: dict[str, set[str]],
    total: int,
) -> list[FunnelBlocker]:
    return [
        FunnelBlocker(
            code=code,
            count=len(review_ids),
            affected_rate=_rate(len(review_ids), total),
            sample_review_ids=sorted(review_ids)[:5],
        )
        for code, review_ids in sorted(
            affected_reviews.items(), key=lambda item: (-len(item[1]), item[0])
        )
        if review_ids
    ]


def _priority_category(code: str) -> tuple[PriorityCategory, RemediationKind]:
    if code == "SERVINGS_MISSING":
        return "servings", "DETERMINISTIC_OR_LLM_ASSIST"
    if code == "TIME_MISSING":
        return "prep_time", "DETERMINISTIC_OR_LLM_ASSIST"
    if code == "MEAL_SLOTS_MISSING":
        return "meal_slots", "DETERMINISTIC_OR_LLM_ASSIST"
    if code == "COOKING_STEPS_MISSING" or code.startswith(("NON_ACTIONABLE_STEP", "IMAGE_DEPENDENT_STEP", "MEDICAL_STEP_TEXT")):
        remediation: RemediationKind = "HUMAN_REVIEW" if code.startswith("MEDICAL_STEP_TEXT") else "DETERMINISTIC_OR_LLM_ASSIST"
        return "cooking_steps", remediation
    if code == "INGREDIENT_MAPPING_INCOMPLETE":
        return "ingredient_identity", "HUMAN_REVIEW"
    if code in {"INGREDIENT_QUANTITY_INCOMPLETE", "NUTRITION_QUANTITY_INCOMPLETE", "NUTRITION_NOT_CALCULABLE"}:
        return "ingredient_quantity", "TRUSTED_DATA_ENRICHMENT"
    if code == "ALLERGEN_COMPOSITION_INCOMPLETE":
        return "allergen_composition", "TRUSTED_DATA_ENRICHMENT"
    if code == "NUTRITION_COVERAGE_INCOMPLETE":
        return "nutrition_coverage", "TRUSTED_DATA_ENRICHMENT"
    return "other", "HUMAN_REVIEW"


def _priorities(
    publication_affected: dict[str, set[str]],
    solver_affected: dict[str, set[str]],
    total: int,
) -> list[FunnelPriority]:
    grouped_reviews: dict[tuple[FunnelStage, PriorityCategory, RemediationKind], set[str]] = defaultdict(set)
    grouped_codes: dict[tuple[FunnelStage, PriorityCategory, RemediationKind], set[str]] = defaultdict(set)
    for stage, affected in (("PUBLICATION", publication_affected), ("SOLVER", solver_affected)):
        for code, review_ids in affected.items():
            category, remediation = _priority_category(code)
            key = (stage, category, remediation)
            grouped_reviews[key].update(review_ids)
            grouped_codes[key].add(code)
    values = [
        FunnelPriority(
            category=category,
            stage=stage,
            count=len(review_ids),
            affected_rate=_rate(len(review_ids), total),
            reason_codes=sorted(grouped_codes[(stage, category, remediation)]),
            remediation=remediation,
        )
        for (stage, category, remediation), review_ids in grouped_reviews.items()
        if review_ids
    ]
    # Publication blockers drive the next automation step before Solver-only enrichment.
    return sorted(values, key=lambda item: (0 if item.stage == "PUBLICATION" else 1, -item.count, item.category))


def audit_review_corpus(
    reviews: list[RecipeReviewItem],
    foods: list[FoodNutrition],
    *,
    generated_at: datetime | None = None,
) -> RecipeCorpusFunnelReport:
    """Re-run the current deterministic gate over persisted raw+curation records.

    The audit is deliberately read-only. Stored drafts/reports are evidence for
    drift comparison only; every funnel decision comes from a fresh
    ``build_quality_draft`` call under the current quality gate.
    """

    lifecycle_counts: Counter[str] = Counter()
    gate_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    publication_affected: dict[str, set[str]] = defaultdict(set)
    solver_affected: dict[str, set[str]] = defaultdict(set)
    records: list[FunnelRecord] = []
    drift_records: list[FunnelDriftRecord] = []

    for item in sorted(reviews, key=lambda value: value.review_id):
        _draft, report = build_quality_draft(item.raw, item.curation, foods)
        lifecycle_counts[item.status] += 1
        gate_counts[item.quality_report.quality_gate_version] += 1
        status_counts[report.status] += 1
        for code in report.blocking_reasons:
            publication_affected[code].add(item.review_id)
        for code in report.solver_blocking_reasons:
            solver_affected[code].add(item.review_id)
        if (
            item.quality_report.quality_gate_version != QUALITY_GATE_VERSION
            or item.quality_report.status != report.status
            or item.quality_report.blocking_reasons != report.blocking_reasons
            or item.quality_report.solver_blocking_reasons != report.solver_blocking_reasons
        ):
            drift_records.append(FunnelDriftRecord(
                review_id=item.review_id,
                source_id=item.raw.source_id,
                stored_quality_gate_version=item.quality_report.quality_gate_version,
                stored_status=item.quality_report.status,
                recomputed_status=report.status,
            ))
        records.append(FunnelRecord(
            review_id=item.review_id,
            source_id=item.raw.source_id,
            title=item.raw.title,
            lifecycle_status=item.status,
            processing_stage=item.processing_stage,
            stored_quality_gate_version=item.quality_report.quality_gate_version,
            stored_status=item.quality_report.status,
            recomputed_status=report.status,
            publication_blocking_reasons=report.blocking_reasons,
            solver_blocking_reasons=report.solver_blocking_reasons,
            unspecified_quantity_names=report.unspecified_quantity_names,
        ))

    total = len(reviews)
    blocked = status_counts["BLOCKED"]
    publication_ready = status_counts["PUBLICATION_READY"]
    solver_ready = status_counts["SOLVER_READY"]
    publishable = publication_ready + solver_ready
    return RecipeCorpusFunnelReport(
        generated_at=generated_at or datetime.now(timezone.utc),
        quality_gate_version=QUALITY_GATE_VERSION,
        total_reviews=total,
        lifecycle_counts=dict(sorted(lifecycle_counts.items())),
        stored_gate_version_counts=dict(sorted(gate_counts.items())),
        recomputed_status_counts={
            "BLOCKED": blocked,
            "PUBLICATION_READY": publication_ready,
            "SOLVER_READY": solver_ready,
        },
        blocked_count=blocked,
        publication_ready_count=publication_ready,
        solver_ready_count=solver_ready,
        publishable_count=publishable,
        publishable_rate=_rate(publishable, total),
        solver_ready_rate=_rate(solver_ready, total),
        solver_within_publishable_rate=_rate(solver_ready, publishable),
        publication_blockers=_rank_blockers(publication_affected, total),
        solver_blockers=_rank_blockers(solver_affected, total),
        priorities=_priorities(publication_affected, solver_affected, total),
        drift_count=len(drift_records),
        drift_records=drift_records,
        records=records,
    )
