import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from mealpilot.domain.models import (
    CookingStep,
    IngredientQuantityKind,
    Nutrition,
    QuantityOrigin,
    Recipe,
    RecipeIngredient,
    SourceMetadata,
)
from mealpilot.ingestion.admin import AdminIngredientPatch, AdminReviewCurationCommand, build_trusted_curation
from mealpilot.ingestion.quality import RecipeCuration, build_quality_draft
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe, RawRecipeIngredient
from mealpilot.nutrition.loaders import load_food_catalog


FOODS_PATH = Path("data/nutrition/foods.sample.json")


def _raw(*ingredients: RawRecipeIngredient) -> RawMeishiChinaRecipe:
    digest = hashlib.sha256("m40-e-display-tolerant".encode("utf-8")).hexdigest()
    return RawMeishiChinaRecipe(
        staging_id=f"raw-mc-{digest[:24]}",
        captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        source_id="meishichina:990040",
        source_url="https://home.meishichina.com/recipe-990040.html",
        raw_content_hash=digest,
        title="数量缺失展示测试",
        ingredients=list(ingredients),
        cooking_steps=[CookingStep(step_number=1, instruction="将食材洗净切好后放入锅中炒熟。")],
        source_time_label="十分钟",
        categories=["午餐"],
    )


def _curation() -> RecipeCuration:
    return RecipeCuration(servings=Decimal("2"))


def test_missing_main_quantity_is_publishable_but_solver_blocked() -> None:
    raw = _raw(RawRecipeIngredient(group="main", raw_name="牛肉", raw_amount="", raw_text="牛肉"))
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))

    ingredient = draft.ingredients[0]
    assert ingredient.quantity_kind == IngredientQuantityKind.UNSPECIFIED
    assert ingredient.quantity_origin == QuantityOrigin.DISPLAY_FALLBACK
    assert ingredient.display_quantity == "用量未注明"
    assert ingredient.amount_g is None
    assert report.status == "PUBLICATION_READY"
    assert report.blocking_reasons == []
    assert report.unspecified_quantity_names == ["牛肉"]
    assert "UNSPECIFIED_QUANTITY_PUBLICATION_ONLY" in report.warnings
    assert "NUTRITION_QUANTITY_INCOMPLETE" in report.solver_blocking_reasons


def test_missing_seasoning_quantity_gets_display_only_appropriate_amount() -> None:
    raw = _raw(RawRecipeIngredient(group="seasoning", raw_name="盐", raw_amount="", raw_text="盐"))
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))

    ingredient = draft.ingredients[0]
    assert ingredient.quantity_kind == IngredientQuantityKind.UNSPECIFIED
    assert ingredient.quantity_origin == QuantityOrigin.DISPLAY_FALLBACK
    assert ingredient.display_quantity == "适量"
    assert ingredient.amount_g is None
    assert report.status == "PUBLICATION_READY"
    assert "NUTRITION_QUANTITY_INCOMPLETE" in report.solver_blocking_reasons


def test_source_ambiguous_quantity_is_preserved_without_claiming_fallback() -> None:
    raw = _raw(RawRecipeIngredient(group="main", raw_name="牛肉", raw_amount="若干", raw_text="牛肉 若干"))
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))

    ingredient = draft.ingredients[0]
    assert ingredient.quantity_kind == IngredientQuantityKind.UNSPECIFIED
    assert ingredient.quantity_origin == QuantityOrigin.SOURCE_EXPLICIT
    assert ingredient.display_quantity == "若干"
    assert report.status == "PUBLICATION_READY"
    assert report.blocking_reasons == []


def test_source_explicit_qualitative_quantity_keeps_existing_semantics() -> None:
    raw = _raw(RawRecipeIngredient(group="seasoning", raw_name="盐", raw_amount="少许", raw_text="盐 少许"))
    draft, report = build_quality_draft(raw, _curation(), load_food_catalog(FOODS_PATH))

    ingredient = draft.ingredients[0]
    assert ingredient.quantity_kind == IngredientQuantityKind.QUALITATIVE
    assert ingredient.quantity_origin == QuantityOrigin.SOURCE_EXPLICIT
    assert ingredient.display_quantity == "少许"
    assert report.status == "PUBLICATION_READY"


def test_admin_supplied_quantity_is_marked_reviewer_confirmed() -> None:
    raw = _raw(RawRecipeIngredient(group="main", raw_name="牛肉", raw_amount="", raw_text="牛肉"))
    command = AdminReviewCurationCommand(
        expected_review_version=0,
        servings=Decimal("2"),
        supported_slots=["lunch", "dinner"],
        prep_minutes=10,
        ingredients=[AdminIngredientPatch(raw_name="牛肉", canonical_id="beef", amount=Decimal("180"), unit="g")],
    )
    curation = build_trusted_curation(raw, command)
    assert curation.ingredient_overrides[0].quantity_origin == QuantityOrigin.REVIEWER_CONFIRMED

    draft, _ = build_quality_draft(raw, curation, load_food_catalog(FOODS_PATH))
    assert draft.ingredients[0].quantity_kind == IngredientQuantityKind.MEASURED
    assert draft.ingredients[0].quantity_origin == QuantityOrigin.REVIEWER_CONFIRMED
    assert draft.ingredients[0].amount_g == Decimal("180")


def test_review_materialization_preserves_display_fallback_provenance(tmp_path: Path) -> None:
    raw = _raw(RawRecipeIngredient(group="main", raw_name="牛肉", raw_amount="", raw_text="牛肉"))
    service = ReviewService(ReviewStore(tmp_path / "reviews"), load_food_catalog(FOODS_PATH))
    item = service.prepare(raw, actor="crawler")
    item = service.curate(item.review_id, _curation(), expected_version=0, actor="reviewer")
    assert item.quality_report.status == "PUBLICATION_READY"
    item = service.store.update(item.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=1)
    item = service.approve(item.review_id, expected_version=2, actor="reviewer")

    assert item.staged_recipe is not None
    published_ingredient = item.staged_recipe.recipe.ingredients[0]
    assert published_ingredient.quantity_kind == IngredientQuantityKind.UNSPECIFIED
    assert published_ingredient.quantity_origin == QuantityOrigin.DISPLAY_FALLBACK
    assert published_ingredient.display_quantity == "用量未注明"
    assert published_ingredient.amount_g is None
    assert item.staged_recipe.recipe.solver_eligible is False


def test_domain_rejects_solver_ready_recipe_with_unspecified_quantity() -> None:
    ingredient = RecipeIngredient(
        canonical_id="beef",
        canonical_name="牛肉",
        display_quantity="用量未注明",
        quantity_kind=IngredientQuantityKind.UNSPECIFIED,
        quantity_origin=QuantityOrigin.DISPLAY_FALLBACK,
        amount_g=None,
        allergens=[],
    )
    with pytest.raises(ValidationError, match="solver-eligible recipe cannot contain unspecified quantities"):
        Recipe(
            recipe_id="recipe-invalid-unspecified",
            version="1",
            title="不应进入 Solver",
            supported_slots=["lunch"],
            servings=Decimal("1"),
            prep_minutes=10,
            nutrition_per_serving=Nutrition(
                energy_kcal=Decimal("500"),
                protein_g=Decimal("30"),
                carbohydrate_g=Decimal("50"),
                fat_g=Decimal("15"),
            ),
            nutrition_basis="SOURCE_DECLARED",
            solver_eligible=True,
            ingredients=[ingredient],
            source=SourceMetadata(
                source_id="test",
                source_url="https://example.com/recipe",
                license="test",
                data_version="v1",
            ),
            numeric_policy_version="test",
        )
