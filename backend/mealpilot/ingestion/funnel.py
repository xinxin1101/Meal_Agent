"""Read-only recomputation and funnel metrics for the real recipe review corpus."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.ingestion.quality import QUALITY_GATE_VERSION, RecipeQualityReport, build_quality_draft
from mealpilot.ingestion.review import RecipeReviewItem
from mealpilot.nutrition.catalog import FoodNutrition


REPORT_VERSION = "m40-f-v1"


class FunnelBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    count: int = Field(ge=1)
    affected_rate: Decimal = Field(ge=0, le=1)
    sample_review_ids: list[str] = Field(default_factory=list, max_length=5)


class FunnelPriority(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[
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
    stage: Literal["PUBLICATION", "SOLVER"]
    count: int = Field(ge=1)
    affected_rate: Decimal = Field(ge=0, le=1)
    reason_codes: list[str] = Field(min_length=1)
    remediation: Literal["DETERMINISTIC_OR_LLM_ASSIST", "TRUSTED_DATA_ENRICHMENT", "HUMAN_REVIEW"]


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
    counts: Counter[str],
    samples: dict[str, list[str]],
    total: int,
) -> list[FunnelBlocker]:
    return [
        FunnelBlocker(
            code=code,
            count=count,
            affected_rate=_rate(count, total),
            sample_review_ids=samples.get(code, [])[:5],
        )
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _priority_category(code: str) -> tuple[str, str]:
    if code == "SERVINGS_MISSING":
        return "servings", "DETERMINISTIC_OR_LLM_ASSIST"
    if code == "TIME_MISSING":
        return "prep_time", "DETERMINISTIC_OR_LLM_ASSIST"
    if code == "MEAL_SLOTS_MISSING":
        return "meal_slots", "DETERMINISTIC_OR_LLM_ASSIST"
    if code == "COOKING_STEPS_MISSING" or code.startswith(("NON_ACTIONABLE_STEP", "IMAGE_DEPENDENT_STEP", "MEDICAL_STEP_TEXT")):
        return "cooking_steps", "HUMAN_REVIEW" if code.startswith("MEDICAL_STEP_TEXT") else "DETERMINISTIC_OR_LLM_ASSIST"
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
    publication_counts: Counter[str],
    solver_counts: Counter[str],
    total: int,
) -> list[FunnelPriority]:
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    for stage, counts in (("PUBLICATION", publication_counts), ("SOLVER", solver_counts)):
        for code, count in counts.items():
            category, remediation = _priority_category(code)
            key = (stage, category, remediation)
            entry = grouped.setdefault(key, {"count": 0, "codes": []})
            entry["count"] = int(entry["count"]) + count
            cast_codes = entry["codes"]
            assert isinstance(cast_codes, list)
            cast_codes.append(code)
    values = [
        FunnelPriority(
            category=category,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            count=int(value["count"]),
            affected_rate=_rate(int(value["count"]), total),
            reason_codes=sorted(set(value["codes"])),  # type: ignore[arg-type]
            remediation=remediation,  # type: ignore[arg-type]
        )
        for (stage, category, remediation), value in grouped.items()
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
    publication_counts: Counter[str] = Counter()
    solver_counts: Counter[str] = Counter()
    publication_samples: dict[str, list[str]] = defaultdict(list)
    solver_samples: dict[str, list[str]] = defaultdict(list)
    records: list[FunnelRecord] = []
    drift_records: list[FunnelDriftRecord] = []

    for item in sorted(reviews, key=lambda value: value.review_id):
        _draft, report = build_quality_draft(item.raw, item.curation, foods)
        lifecycle_counts[item.status] += 1
        gate_counts[item.quality_report.quality_gate_version] += 1
        status_counts[report.status] += 1
        for code in report.blocking_reasons:
            publication_counts[code] += 1
            if len(publication_samples[code]) < 5:
                publication_samples[code].append(item.review_id)
        for code in report.solver_blocking_reasons:
            solver_counts[code] += 1
            if len(solver_samples[code]) < 5:
                solver_samples[code].append(item.review_id)
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
        publication_blockers=_rank_blockers(publication_counts, publication_samples, total),
        solver_blockers=_rank_blockers(solver_counts, solver_samples, total),
        priorities=_priorities(publication_counts, solver_counts, total),
        drift_count=len(drift_records),
        drift_records=drift_records,
        records=records,
    )
