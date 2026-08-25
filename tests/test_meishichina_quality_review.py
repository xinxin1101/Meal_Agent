import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from mealpilot.data.loader import load_recipe_sets
from mealpilot.domain.models import CookingStep
from mealpilot.ingestion.pipeline import DataQualityError, StagedRecipe, publish, recipe_content_hash
from mealpilot.ingestion.quality import RecipeCuration, build_quality_draft, parse_amount
from mealpilot.ingestion.review import (
    AuthorizationEvidence,
    ReviewConflict,
    ReviewRejected,
    ReviewService,
    ReviewStore,
)
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe, RawRecipeIngredient
from mealpilot.nutrition.loaders import load_food_catalog


FOODS_PATH = Path("data/nutrition/foods.sample.json")


def _raw_recipe(*, ambiguous: bool = False, image_dependent: bool = False) -> RawMeishiChinaRecipe:
    body = "synthetic-meishichina-review-fixture"
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    second_step = "如图放入西兰花炒熟。" if image_dependent else "放入西兰花炒熟。"
    return RawMeishiChinaRecipe(
        staging_id=f"raw-mc-{content_hash[:24]}",
        captured_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        source_id="meishichina:990001",
        source_url="https://home.meishichina.com/recipe-990001.html",
        raw_content_hash=content_hash,
        title="牛肉炒西兰花",
        ingredients=[
            RawRecipeIngredient(
                group="main",
                raw_name="牛肉",
                raw_amount="适量" if ambiguous else "200克",
                raw_text="牛肉 适量" if ambiguous else "牛肉 200克",
            ),
            RawRecipeIngredient(
                group="secondary",
                raw_name="西兰花",
                raw_amount="100克",
                raw_text="西兰花 100克",
            ),
        ],
        cooking_steps=[
            CookingStep(step_number=1, instruction="牛肉洗净切片。"),
            CookingStep(step_number=2, instruction=second_step),
        ],
        technique="炒",
        source_time_label="廿分钟",
        categories=["午餐"],
        tips=["本菜可治疗疾病。"],
        warnings=["COPYRIGHT_AUTHORIZATION_REQUIRED"],
    )


def _curation() -> RecipeCuration:
    return RecipeCuration(servings=Decimal("2"))


def _service(tmp_path: Path) -> ReviewService:
    return ReviewService(
        ReviewStore(tmp_path / "reviews"),
        load_food_catalog(FOODS_PATH),
    )


def _evidence(raw: RawMeishiChinaRecipe, *, evidence_type: str = "WRITTEN_PERMISSION") -> AuthorizationEvidence:
    return AuthorizationEvidence(
        evidence_id=f"evidence-{evidence_type.lower().replace('_', '-')}",
        evidence_type=evidence_type,
        source_id=raw.source_id,
        source_content_hash=raw.raw_content_hash,
        license_identifier="written-permission:test-fixture",
        proof_reference="tests/fixtures/authorization/test-fixture.txt",
        granted_by="fixture rights holder",
        granted_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        scope=["STORE", "DISPLAY", "SOLVER_USE"],
        notes="Synthetic evidence used only by automated tests.",
    )


def test_quality_gate_builds_complete_deterministic_draft() -> None:
    raw = _raw_recipe()
    foods = load_food_catalog(FOODS_PATH)
    draft, report = build_quality_draft(raw, _curation(), foods)
    second_draft, second_report = build_quality_draft(raw, _curation(), foods)

    assert report.status == "SOLVER_READY"
    assert report.blocking_reasons == []
    assert report.image_independent_steps is True
    assert report.nutrition_complete is True
    assert draft.nutrition_per_serving is not None
    assert draft.nutrition_per_serving.energy_kcal == Decimal("267")
    assert draft.prep_minutes == 20
    assert draft.supported_slots == ["lunch", "dinner"]
    assert draft.removed_medical_text_count == 1
    assert "MEDICAL_TIPS_EXCLUDED" in report.warnings
    assert draft == second_draft
    assert report.report_hash == second_report.report_hash


def test_source_quantity_parser_accepts_only_explicit_mass_and_stable_source_typos() -> None:
    assert parse_amount("1块（200克）") == (Decimal("200"), "g", None)
    assert parse_amount("1斤左古") == (Decimal("500"), "g", None)
    assert parse_amount("500亳升") == (Decimal("500"), "ml", None)
    amount, unit, warning = parse_amount("2个")
    assert (amount, unit) == (Decimal("2"), "count")
    assert warning is None


def test_step_gate_accepts_clear_chinese_actions_and_excludes_labels() -> None:
    raw = _raw_recipe().model_copy(update={
        "ingredients": [
            RawRecipeIngredient(group="main", raw_name="鸭肉", raw_amount="200克", raw_text="鸭肉 200克"),
            RawRecipeIngredient(group="seasoning", raw_name="干辣椒", raw_amount="少许", raw_text="干辣椒 少许"),
            RawRecipeIngredient(group="seasoning", raw_name="花椒", raw_amount="少许", raw_text="花椒 少许"),
        ],
        "cooking_steps": [
            CookingStep(step_number=1, instruction="鸭肉斩块备用。"),
            CookingStep(step_number=2, instruction="盖上盖子中小火焗十分钟左右。"),
            CookingStep(step_number=3, instruction="干辣椒、花椒。"),
            CookingStep(step_number=4, instruction="成品图。"),
        ],
    })
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))
    assert not any(reason.startswith("NON_ACTIONABLE_STEP") for reason in report.blocking_reasons)
    assert [step.instruction for step in draft.cooking_steps] == ["鸭肉斩块备用。", "盖上盖子中小火焗十分钟左右。"]
    assert draft.removed_presentation_step_numbers == [3, 4]


def test_quality_gate_allows_qualitative_amount_for_publication_but_not_solver() -> None:
    draft, report = build_quality_draft(
        _raw_recipe(ambiguous=True),
        _curation(),
        load_food_catalog(FOODS_PATH),
    )
    assert report.status == "PUBLICATION_READY"
    assert report.blocking_reasons == []
    assert "NUTRITION_QUANTITY_INCOMPLETE" in report.solver_blocking_reasons
    assert draft.ingredients[0].display_quantity == "适量"
    assert draft.ingredients[0].quantity_kind == "QUALITATIVE"
    assert draft.nutrition_per_serving is None


def test_raw_ingredient_name_is_a_stable_identity_without_manual_mapping() -> None:
    raw = _raw_recipe(ambiguous=True).model_copy(update={
        "ingredients": [
            RawRecipeIngredient(group="main", raw_name="鸡爪", raw_amount="适量", raw_text="鸡爪 适量"),
            RawRecipeIngredient(group="seasoning", raw_name="干辣椒", raw_amount="适量", raw_text="干辣椒 适量"),
            RawRecipeIngredient(group="seasoning", raw_name="小米椒", raw_amount="少许", raw_text="小米椒 少许"),
        ],
    })
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))
    assert [item.canonical_name for item in draft.ingredients] == ["鸡爪", "干辣椒", "小米椒"]
    assert all(item.canonical_id for item in draft.ingredients)
    assert "INGREDIENT_MAPPING_INCOMPLETE" not in report.blocking_reasons
    assert "ALLERGEN_COMPOSITION_INCOMPLETE" not in report.blocking_reasons
    assert report.status == "PUBLICATION_READY"
    assert draft.solver_eligible is False


def test_unknown_raw_name_keeps_identity_but_fails_closed_on_allergens() -> None:
    raw = _raw_recipe(ambiguous=True).model_copy(update={
        "ingredients": [
            RawRecipeIngredient(group="other", raw_name="来源新食材", raw_amount="适量", raw_text="来源新食材 适量"),
        ],
    })
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))
    assert draft.ingredients[0].canonical_name == "来源新食材"
    assert draft.ingredients[0].canonical_id is not None
    assert "INGREDIENT_MAPPING_INCOMPLETE" not in report.blocking_reasons
    assert "ALLERGEN_COMPOSITION_INCOMPLETE" not in report.blocking_reasons
    assert "ALLERGEN_COMPOSITION_INCOMPLETE" in report.solver_blocking_reasons
    assert report.status == "PUBLICATION_READY"
    assert draft.solver_eligible is False


def test_publication_ready_recipe_can_be_approved_but_stays_out_of_solver(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe(ambiguous=True), actor="crawler")
    item = service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")
    assert item.quality_report.status == "PUBLICATION_READY"
    item = service.store.update(item.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=1)
    item = service.approve(item.review_id, expected_version=2, actor="reviewer")
    assert item.staged_recipe is not None
    assert item.staged_recipe.quality_gate_status == "PUBLICATION_READY"
    assert item.staged_recipe.recipe.solver_eligible is False


def test_unknown_allergen_composition_is_publishable_for_display_but_preserved_as_unsafe(tmp_path: Path) -> None:
    raw = _raw_recipe(ambiguous=True).model_copy(update={
        "ingredients": [
            RawRecipeIngredient(group="other", raw_name="来源复合调味品", raw_amount="适量", raw_text="来源复合调味品 适量"),
        ],
    })
    service = _service(tmp_path)
    item = service.prepare(raw, actor="crawler")
    item = service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")
    assert item.quality_report.status == "PUBLICATION_READY"
    assert "ALLERGEN_COMPOSITION_INCOMPLETE" in item.quality_report.solver_blocking_reasons
    item = service.store.update(item.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=1)
    item = service.approve(item.review_id, expected_version=2, actor="reviewer")
    assert item.staged_recipe is not None
    assert item.staged_recipe.recipe.solver_eligible is False
    assert item.staged_recipe.recipe.ingredients[0].allergen_composition_known is False


def test_quality_gate_blocks_image_dependent_step() -> None:
    draft, report = build_quality_draft(
        _raw_recipe(image_dependent=True),
        _curation(),
        load_food_catalog(FOODS_PATH),
    )

    assert report.status == "BLOCKED"
    assert "IMAGE_DEPENDENT_STEP:2" in report.blocking_reasons
    assert report.image_independent_steps is False


def test_readable_recipe_can_publish_without_slots_servings_or_time(tmp_path: Path) -> None:
    raw = _raw_recipe().model_copy(update={"source_time_label": None, "categories": []})
    service = _service(tmp_path)
    item = service.prepare(raw, actor="crawler")
    assert item.quality_report.status == "BLOCKED"
    assert item.quality_report.readable_eligible is True
    assert {"SERVINGS_MISSING", "TIME_MISSING", "MEAL_SLOTS_MISSING"}.issubset(item.quality_report.blocking_reasons)
    item = service.store.update(item.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=item.review_version)
    item = service.publish_readable(item.review_id, item.review_version, "reviewer", tmp_path / "readable.json", "r39-test")
    assert item.readable_publish_receipt is not None
    published = json.loads((tmp_path / "readable.json").read_text(encoding="utf-8"))
    assert published[0]["title"] == raw.title
    assert published[0]["servings"] is None
    assert published[0]["supported_slots"] == []


def test_admin_cannot_publish_before_llm_structure_and_final_validation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="test")
    item = service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")
    with pytest.raises(ReviewRejected, match="FINAL_LLM_STRUCTURE_AND_VALIDATION_REQUIRED"):
        service.approve(item.review_id, expected_version=1, actor="reviewer")


def test_manual_revalidation_does_not_hide_an_unresolved_llm_failure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="test")
    item = service.record_llm_failure(item.review_id, item.review_version, "worker", "LLM_PROVIDER_FAILED")
    item = service.curate(item.review_id, _curation(), item.review_version, "reviewer")
    assert item.processing_stage == "LLM_FAILED"
    assert item.processing_errors == ["LLM_PROVIDER_FAILED"]


def test_review_approval_publish_and_revocation_flow(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="crawler")
    assert item.status == "PENDING"
    assert item.quality_report.status == "BLOCKED"

    item = service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")
    assert item.quality_report.status == "SOLVER_READY"

    with pytest.raises(ReviewConflict, match="STALE_REVIEW_VERSION"):
        service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")

    item = service.store.update(item.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=1)
    item = service.approve(item.review_id, expected_version=2, actor="reviewer")
    assert item.status == "APPROVED"
    assert item.staged_recipe is not None
    assert item.staged_recipe.quality_gate_status == "SOLVER_READY"
    assert item.staged_recipe.authorization_evidence_ids == []
    assert item.staged_recipe.recipe.source.license == "PERSONAL_STUDY_INTERNAL"

    published_path = tmp_path / "recipes.published.json"
    item = service.publish(
        item.review_id,
        expected_version=3,
        actor="publisher",
        published_path=published_path,
        dataset_version="test-v1",
    )
    assert item.status == "PUBLISHED"
    published = load_recipe_sets(published_path)
    assert [recipe.recipe_id for recipe in published] == ["recipe-meishichina-990001-v1"]

    item = service.revoke(
        item.review_id,
        expected_version=4,
        actor="publisher",
        reason="authorization withdrawn",
        published_path=published_path,
        dataset_version="test-v2",
    )
    assert item.status == "REVOKED"
    assert json.loads(published_path.read_text(encoding="utf-8")) == []


def test_evidence_must_match_exact_source_hash(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe())
    evidence = _evidence(item.raw).model_copy(update={"source_content_hash": "0" * 64})
    with pytest.raises(ReviewRejected, match="EVIDENCE_SOURCE_MISMATCH"):
        service.add_evidence(item.review_id, evidence, expected_version=0, actor="reviewer")


def test_pipeline_rejects_direct_meishichina_publication_without_final_quality_gate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe())
    item = service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")
    item = service.store.update(item.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=1)
    item = service.approve(item.review_id, expected_version=2, actor="reviewer")
    assert item.staged_recipe is not None

    unsafe = StagedRecipe(
        staging_id=item.staged_recipe.staging_id,
        captured_at=item.staged_recipe.captured_at,
        raw_content_hash=recipe_content_hash(item.staged_recipe.recipe),
        source_content_hash=item.raw.raw_content_hash,
        license_status="APPROVED",
        review_status="APPROVED",
        raw_ingredient_text=item.staged_recipe.raw_ingredient_text,
        recipe=item.staged_recipe.recipe,
    )
    with pytest.raises(DataQualityError, match="QUALITY_GATE_NOT_READY"):
        publish(unsafe, tmp_path / "unsafe.json", "test")


def test_reject_records_reason_and_prevents_later_approval(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw_recipe())
    item = service.reject(item.review_id, expected_version=0, actor="reviewer", reason="source page incomplete")
    assert item.status == "REJECTED"
    assert item.events[-1].reason == "source page incomplete"
    with pytest.raises(ReviewRejected, match="ONLY_PENDING_REVIEW_CAN_BE_APPROVED"):
        service.approve(item.review_id, expected_version=1, actor="reviewer")
