"""Recipe structure review, deterministic validation, publication and withdrawal."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.domain.models import Recipe, RecipeIngredient, SourceMetadata
from mealpilot.ingestion.pipeline import PublishReceipt, StagedRecipe, WithdrawalReceipt, publish, recipe_content_hash, withdraw
from mealpilot.ingestion.quality import RecipeCuration, RecipeQualityReport, StructuredRecipeDraft, build_quality_draft
from mealpilot.ingestion.llm_assist import LlmAssistanceRecord, apply_proposal, request_curation_proposal
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe
from mealpilot.nutrition.catalog import FoodNutrition


REVIEW_ID = re.compile(r"^review-mc-\d+-[a-f0-9]{8}$")
QUALIFYING_EVIDENCE = {"WRITTEN_PERMISSION", "OPEN_LICENSE", "FIRST_PARTY", "PUBLIC_DOMAIN"}
REQUIRED_SCOPE = {"STORE", "DISPLAY", "SOLVER_USE"}


class ReviewConflict(RuntimeError):
    pass


class ReviewRejected(RuntimeError):
    pass


class AuthorizationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,79}$")
    evidence_type: Literal["WRITTEN_PERMISSION", "OPEN_LICENSE", "FIRST_PARTY", "PUBLIC_DOMAIN", "PERSONAL_STUDY_DECLARATION"]
    source_id: str
    source_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_identifier: str = Field(min_length=1, max_length=200)
    proof_reference: str = Field(min_length=1, max_length=1_000)
    granted_by: str = Field(min_length=1, max_length=200)
    granted_at: datetime
    expires_at: datetime | None = None
    scope: list[Literal["STORE", "MODIFY", "DISPLAY", "SOLVER_USE", "REDISTRIBUTE"]]
    notes: str = Field(default="", max_length=1_000)


class ReviewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: Literal["CREATED", "LLM_ASSISTED", "LLM_FAILED", "CURATED", "EVIDENCE_ADDED", "APPROVED", "REJECTED", "PUBLISHED", "REVOKED"]
    occurred_at: datetime
    actor: str
    reason: str | None = None


class RecipeReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    review_version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    status: Literal["PENDING", "APPROVED", "REJECTED", "PUBLISHED", "REVOKED"]
    processing_stage: Literal["INITIAL_VALIDATED", "LLM_FAILED", "FINAL_VALIDATION_BLOCKED", "FINAL_VALIDATED"] = "INITIAL_VALIDATED"
    processing_errors: list[str] = Field(default_factory=list)
    source_use_scope: Literal["PERSONAL_STUDY_INTERNAL"] = "PERSONAL_STUDY_INTERNAL"
    raw: RawMeishiChinaRecipe
    curation: RecipeCuration
    draft: StructuredRecipeDraft
    quality_report: RecipeQualityReport
    llm_assistance: LlmAssistanceRecord | None = None
    authorization_evidence: list[AuthorizationEvidence] = Field(default_factory=list)
    staged_recipe: StagedRecipe | None = None
    publish_receipt: PublishReceipt | None = None
    withdrawal_receipt: WithdrawalReceipt | None = None
    events: list[ReviewEvent]


class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    review_version: int
    status: str
    source_id: str
    title: str
    quality_status: str
    blocking_reasons: list[str]
    evidence_count: int


def evidence_qualifies(evidence: AuthorizationEvidence, raw: RawMeishiChinaRecipe, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    return (
        evidence.evidence_type in QUALIFYING_EVIDENCE
        and evidence.source_id == raw.source_id
        and evidence.source_content_hash == raw.raw_content_hash
        and REQUIRED_SCOPE.issubset(evidence.scope)
        and (evidence.expires_at is None or evidence.expires_at > current)
    )


class ReviewStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, review_id: str) -> Path:
        if not REVIEW_ID.fullmatch(review_id):
            raise ValueError("review_id is invalid")
        return self.root / f"{review_id}.json"

    def create(self, item: RecipeReviewItem) -> RecipeReviewItem:
        path = self._path(item.review_id)
        if path.exists():
            raise ReviewConflict("REVIEW_ALREADY_EXISTS")
        self._write(path, item)
        return item

    def get(self, review_id: str) -> RecipeReviewItem:
        path = self._path(review_id)
        if not path.exists():
            raise KeyError(review_id)
        return RecipeReviewItem.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[RecipeReviewItem]:
        if not self.root.exists():
            return []
        return [RecipeReviewItem.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(self.root.glob("review-mc-*.json"))]

    def update(self, item: RecipeReviewItem, expected_version: int) -> RecipeReviewItem:
        current = self.get(item.review_id)
        if current.review_version != expected_version:
            raise ReviewConflict("STALE_REVIEW_VERSION")
        updated = item.model_copy(update={"review_version": expected_version + 1, "updated_at": datetime.now(timezone.utc)})
        self._write(self._path(item.review_id), updated)
        return updated

    def _write(self, path: Path, item: RecipeReviewItem) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(item.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)


class ReviewService:
    def __init__(self, store: ReviewStore, foods: list[FoodNutrition]) -> None:
        self.store = store
        self.foods = foods

    def prepare(self, raw: RawMeishiChinaRecipe, actor: str = "system") -> RecipeReviewItem:
        curation = RecipeCuration()
        draft, report = build_quality_draft(raw, curation, self.foods)
        now = datetime.now(timezone.utc)
        item = RecipeReviewItem(
            review_id=f"review-mc-{raw.source_id.rsplit(':', 1)[1]}-{raw.raw_content_hash[:8]}",
            review_version=0,
            created_at=now,
            updated_at=now,
            status="PENDING",
            raw=raw,
            curation=curation,
            draft=draft,
            quality_report=report,
            processing_stage="INITIAL_VALIDATED",
            events=[ReviewEvent(event_type="CREATED", occurred_at=now, actor=actor)],
        )
        return self.store.create(item)

    def summaries(self) -> list[ReviewSummary]:
        return [
            ReviewSummary(
                review_id=item.review_id,
                review_version=item.review_version,
                status=item.status,
                source_id=item.raw.source_id,
                title=item.raw.title,
                quality_status=item.quality_report.status,
                blocking_reasons=item.quality_report.blocking_reasons,
                evidence_count=len(item.authorization_evidence),
            )
            for item in self.store.list()
        ]

    def curate(self, review_id: str, curation: RecipeCuration, expected_version: int, actor: str) -> RecipeReviewItem:
        item = self.store.get(review_id)
        if item.status != "PENDING":
            raise ReviewRejected("ONLY_PENDING_REVIEW_CAN_BE_CURATED")
        draft, report = build_quality_draft(item.raw, curation, self.foods)
        event = ReviewEvent(event_type="CURATED", occurred_at=datetime.now(timezone.utc), actor=actor)
        next_stage = item.processing_stage
        processing_errors = item.processing_errors
        if item.llm_assistance is not None:
            next_stage = "FINAL_VALIDATED" if report.status != "BLOCKED" else "FINAL_VALIDATION_BLOCKED"
            processing_errors = []
        updated = item.model_copy(update={"curation": curation, "draft": draft, "quality_report": report, "processing_stage": next_stage, "processing_errors": processing_errors, "events": [*item.events, event]})
        return self.store.update(updated, expected_version)

    def assist(self, review_id: str, expected_version: int, actor: str) -> RecipeReviewItem:
        """Ask the LLM for a typed proposal, then immediately rerun deterministic gates."""
        item = self.store.get(review_id)
        if item.status != "PENDING":
            raise ReviewRejected("ONLY_PENDING_REVIEW_CAN_BE_ASSISTED")
        try:
            record = request_curation_proposal(item.raw)
            curation, record = apply_proposal(item.raw, record)
        except (RuntimeError, ValueError) as error:
            raise ReviewRejected(str(error)) from error
        draft, report = build_quality_draft(item.raw, curation, self.foods)
        event = ReviewEvent(event_type="LLM_ASSISTED", occurred_at=datetime.now(timezone.utc), actor=actor)
        updated = item.model_copy(update={
            "curation": curation,
            "draft": draft,
            "quality_report": report,
            "llm_assistance": record,
            "processing_stage": "FINAL_VALIDATED" if report.status != "BLOCKED" else "FINAL_VALIDATION_BLOCKED",
            "processing_errors": [],
            "events": [*item.events, event],
        })
        return self.store.update(updated, expected_version)

    def record_llm_failure(self, review_id: str, expected_version: int, actor: str, error_code: str) -> RecipeReviewItem:
        item = self.store.get(review_id)
        if item.status != "PENDING":
            raise ReviewRejected("ONLY_PENDING_REVIEW_CAN_RECORD_PROCESSING_FAILURE")
        safe_codes = {
            "SILICONFLOW_NOT_CONFIGURED",
            "LLM_PROVIDER_FAILED",
            "LLM_EMPTY_RESPONSE",
            "LLM_OUTPUT_SCHEMA_INVALID",
            "LLM_RESPONSE_INCOMPLETE",
            "LLM_LEGACY_PATCH_NOT_ALLOWED",
            "LLM_INGREDIENT_COVERAGE_MISMATCH",
            "LLM_INGREDIENT_SOURCE_BINDING_INVALID",
            "LLM_STEP_COVERAGE_MISMATCH",
            "LLM_CANONICAL_ID_NOT_ALLOWED",
        }
        safe_code = error_code if error_code in safe_codes else "LLM_PROCESSING_FAILED"
        event = ReviewEvent(event_type="LLM_FAILED", occurred_at=datetime.now(timezone.utc), actor=actor, reason=safe_code)
        updated = item.model_copy(update={"processing_stage": "LLM_FAILED", "processing_errors": [safe_code], "events": [*item.events, event]})
        return self.store.update(updated, expected_version)

    def add_evidence(self, review_id: str, evidence: AuthorizationEvidence, expected_version: int, actor: str) -> RecipeReviewItem:
        item = self.store.get(review_id)
        if item.status != "PENDING":
            raise ReviewRejected("ONLY_PENDING_REVIEW_ACCEPTS_EVIDENCE")
        if evidence.source_id != item.raw.source_id or evidence.source_content_hash != item.raw.raw_content_hash:
            raise ReviewRejected("EVIDENCE_SOURCE_MISMATCH")
        if evidence.evidence_id in {value.evidence_id for value in item.authorization_evidence}:
            raise ReviewRejected("EVIDENCE_ALREADY_EXISTS")
        event = ReviewEvent(event_type="EVIDENCE_ADDED", occurred_at=datetime.now(timezone.utc), actor=actor)
        updated = item.model_copy(update={"authorization_evidence": [*item.authorization_evidence, evidence], "events": [*item.events, event]})
        return self.store.update(updated, expected_version)

    def approve(self, review_id: str, expected_version: int, actor: str) -> RecipeReviewItem:
        item = self.store.get(review_id)
        if item.status != "PENDING":
            raise ReviewRejected("ONLY_PENDING_REVIEW_CAN_BE_APPROVED")
        if item.quality_report.status not in {"PUBLICATION_READY", "SOLVER_READY"}:
            raise ReviewRejected("PUBLICATION_READY_REQUIRED")
        if item.processing_stage != "FINAL_VALIDATED":
            raise ReviewRejected("FINAL_LLM_STRUCTURE_AND_VALIDATION_REQUIRED")
        recipe = self._to_recipe(item.draft)
        staged = StagedRecipe(
            staging_id=item.raw.staging_id,
            captured_at=item.raw.captured_at,
            raw_content_hash=recipe_content_hash(recipe),
            source_content_hash=item.raw.raw_content_hash,
            license_status="APPROVED",
            review_status="APPROVED",
            raw_ingredient_text=[value.raw_text for value in item.raw.ingredients],
            normalization_warnings=item.quality_report.warnings,
            quality_gate_status=item.quality_report.status,
            quality_gate_version=item.quality_report.quality_gate_version,
            quality_report_hash=item.quality_report.report_hash,
            authorization_evidence_ids=[],
            recipe=recipe,
        )
        event = ReviewEvent(event_type="APPROVED", occurred_at=datetime.now(timezone.utc), actor=actor)
        updated = item.model_copy(update={"status": "APPROVED", "staged_recipe": staged, "events": [*item.events, event]})
        return self.store.update(updated, expected_version)

    def reject(self, review_id: str, expected_version: int, actor: str, reason: str) -> RecipeReviewItem:
        if not reason.strip():
            raise ValueError("rejection reason is required")
        item = self.store.get(review_id)
        if item.status != "PENDING":
            raise ReviewRejected("ONLY_PENDING_REVIEW_CAN_BE_REJECTED")
        event = ReviewEvent(event_type="REJECTED", occurred_at=datetime.now(timezone.utc), actor=actor, reason=reason.strip())
        return self.store.update(item.model_copy(update={"status": "REJECTED", "events": [*item.events, event]}), expected_version)

    def publish(self, review_id: str, expected_version: int, actor: str, published_path: Path, dataset_version: str) -> RecipeReviewItem:
        item = self.store.get(review_id)
        if item.status != "APPROVED" or item.staged_recipe is None:
            raise ReviewRejected("REVIEW_NOT_APPROVED")
        receipt = publish(item.staged_recipe, published_path, dataset_version)
        event = ReviewEvent(event_type="PUBLISHED", occurred_at=datetime.now(timezone.utc), actor=actor)
        updated = item.model_copy(update={"status": "PUBLISHED", "publish_receipt": receipt, "events": [*item.events, event]})
        return self.store.update(updated, expected_version)

    def revoke(
        self,
        review_id: str,
        expected_version: int,
        actor: str,
        reason: str,
        published_path: Path | None = None,
        dataset_version: str = "unknown",
    ) -> RecipeReviewItem:
        if not reason.strip():
            raise ValueError("revocation reason is required")
        item = self.store.get(review_id)
        if item.status not in {"APPROVED", "PUBLISHED"} or item.staged_recipe is None:
            raise ReviewRejected("ONLY_APPROVED_OR_PUBLISHED_REVIEW_CAN_BE_REVOKED")
        withdrawal_receipt = None
        if item.status == "PUBLISHED":
            if published_path is None:
                raise ReviewRejected("PUBLISHED_PATH_REQUIRED_FOR_REVOCATION")
            withdrawal_receipt = withdraw(
                item.staged_recipe.recipe.recipe_id,
                item.staged_recipe.recipe.version,
                published_path,
                dataset_version,
            )
        event = ReviewEvent(event_type="REVOKED", occurred_at=datetime.now(timezone.utc), actor=actor, reason=reason.strip())
        updated = item.model_copy(
            update={"status": "REVOKED", "withdrawal_receipt": withdrawal_receipt, "events": [*item.events, event]}
        )
        return self.store.update(updated, expected_version)

    def _to_recipe(self, draft: StructuredRecipeDraft) -> Recipe:
        if draft.servings is None or draft.prep_minutes is None:
            raise ReviewRejected("DRAFT_NOT_MATERIALIZED")
        ingredients: list[RecipeIngredient] = []
        for item in draft.ingredients:
            if item.canonical_id is None or item.canonical_name is None or item.quantity_kind is None:
                raise ReviewRejected("DRAFT_INGREDIENT_INCOMPLETE")
            if draft.solver_eligible and not item.allergen_composition_known:
                raise ReviewRejected("SOLVER_DRAFT_ALLERGEN_COMPOSITION_INCOMPLETE")
            ingredients.append(RecipeIngredient(
                canonical_id=item.canonical_id,
                canonical_name=item.canonical_name,
                display_quantity=item.display_quantity,
                quantity_kind=item.quantity_kind,
                amount_g=item.amount_g,
                nutrition_calculation_role=item.nutrition_calculation_role,
                allergens=item.allergens,
                allergen_composition_known=item.allergen_composition_known,
            ))
        return Recipe(
            recipe_id=draft.recipe_id,
            version=draft.version,
            title=draft.title,
            supported_slots=draft.supported_slots,
            servings=draft.servings,
            prep_minutes=draft.prep_minutes,
            nutrition_per_serving=draft.nutrition_per_serving,
            nutrition_basis=draft.nutrition_basis,
            solver_eligible=draft.solver_eligible,
            ingredients=ingredients,
            cooking_steps=draft.cooking_steps,
            source=SourceMetadata(
                source_id=draft.source_id,
                source_url=draft.source_url,
                license="PERSONAL_STUDY_INTERNAL",
                data_version=draft.source_content_hash[:12],
            ),
            numeric_policy_version=draft.numeric_policy_version,
            nutrition_data_version=draft.nutrition_data_version,
        )


def load_raw_batch(path: Path) -> list[RawMeishiChinaRecipe]:
    if not path.exists() or path.name != "records.jsonl":
        raise ValueError("records.jsonl path is required")
    records: list[RawMeishiChinaRecipe] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                payload.pop("equipment", None)
                payload["warnings"] = [value for value in payload.get("warnings", []) if value != "EQUIPMENT_NOT_DECLARED"]
                records.append(RawMeishiChinaRecipe.model_validate(payload))
            except ValueError as error:
                raise ValueError(f"invalid raw record at line {line_number}") from error
    if not records:
        raise ValueError("raw batch is empty")
    return records
