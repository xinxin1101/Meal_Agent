import json
from decimal import Decimal
from pathlib import Path

import pytest

from mealpilot.domain.models import Nutrition, Recipe, RecipeIngredient, SourceMetadata
from mealpilot.nutrition.catalog import FoodNutrition
from mealpilot.nutrition.promotion import (
    SolverPromotionRejected,
    build_solver_promotion_candidate,
    inspect_catalog_promotion,
    promote_published_recipe,
    sha256_file,
)


def _source() -> SourceMetadata:
    return SourceMetadata(
        source_id="meishichina:101",
        source_url="https://home.meishichina.com/recipe-101.html",
        license="internal-personal-study",
        data_version="source-v1",
    )


def _ingredient(
    canonical_id: str = "egg",
    *,
    amount_g: str | None = "100",
    known: bool = True,
) -> RecipeIngredient:
    return RecipeIngredient(
        canonical_id=canonical_id,
        canonical_name=canonical_id,
        display_quantity=f"{amount_g}g" if amount_g is not None else "适量",
        quantity_kind="MEASURED" if amount_g is not None else "QUALITATIVE",
        amount_g=Decimal(amount_g) if amount_g is not None else None,
        allergens=["egg"] if canonical_id == "egg" else [],
        allergen_composition_known=known,
    )


def _recipe(*ingredients: RecipeIngredient) -> Recipe:
    return Recipe(
        recipe_id="recipe-meishichina-101-v1",
        version="mc-source-v1",
        title="可信测试菜谱",
        supported_slots=["breakfast"],
        servings=Decimal("1"),
        prep_minutes=10,
        nutrition_per_serving=None,
        nutrition_basis=None,
        solver_eligible=False,
        ingredients=list(ingredients) or [_ingredient()],
        cooking_steps=[{"step_number": 1, "instruction": "将食材煮熟。"}],
        source=_source(),
        numeric_policy_version="mc-r3-v3",
    )


def _food(canonical_id: str = "egg") -> FoodNutrition:
    return FoodNutrition(
        canonical_id=canonical_id,
        food_data_id=f"food-{canonical_id}",
        nutrition_per_100g=Nutrition(
            energy_kcal=Decimal("143"),
            protein_g=Decimal("12.6"),
            carbohydrate_g=Decimal("0.7"),
            fat_g=Decimal("9.5"),
        ),
        source=SourceMetadata(
            source_id="trusted-food-source",
            source_url="https://example.org/nutrition/egg",
            license="CC-BY-4.0",
            data_version="2026-08",
        ),
    )


def _write_catalog(path: Path, recipes: list[Recipe]) -> None:
    path.write_text(
        json.dumps([recipe.model_dump(mode="json") for recipe in recipes], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_candidate_materializes_nutrition_without_llm_or_guessing() -> None:
    recipe = _recipe(_ingredient())

    candidate = build_solver_promotion_candidate(recipe, [_food()])

    assert candidate.recipe.solver_eligible is True
    assert candidate.recipe.nutrition_basis == "CALCULATED_FROM_INGREDIENTS"
    assert candidate.recipe.nutrition_data_version == "2026-08"
    assert candidate.recipe.nutrition_per_serving is not None
    assert candidate.recipe.nutrition_per_serving.energy_kcal == Decimal("143")
    assert candidate.previous_version == recipe.version
    assert candidate.proposed_version.startswith(f"{recipe.version}-sr-")
    assert candidate.proposed_recipe_sha256 != candidate.previous_recipe_sha256


def test_candidate_rejects_missing_quantity_allergen_or_formal_nutrition() -> None:
    with pytest.raises(SolverPromotionRejected, match="NUTRITION_QUANTITY_INCOMPLETE"):
        build_solver_promotion_candidate(_recipe(_ingredient(amount_g=None)), [_food()])

    with pytest.raises(SolverPromotionRejected, match="ALLERGEN_COMPOSITION_INCOMPLETE"):
        build_solver_promotion_candidate(_recipe(_ingredient(known=False)), [_food()])

    with pytest.raises(SolverPromotionRejected, match="NUTRITION_COVERAGE_INCOMPLETE"):
        build_solver_promotion_candidate(_recipe(_ingredient("tofu")), [_food()])


def test_promotion_requires_reviewed_candidate_and_catalog_occ(tmp_path: Path) -> None:
    path = tmp_path / "recipes.json"
    original = _recipe(_ingredient())
    _write_catalog(path, [original])
    catalog_sha = sha256_file(path)
    candidate = inspect_catalog_promotion(
        path,
        [_food()],
        recipe_id=original.recipe_id,
        version=original.version,
    )

    with pytest.raises(SolverPromotionRejected, match="REVIEW_CONFIRMATION_REQUIRED"):
        promote_published_recipe(
            path,
            [_food()],
            recipe_id=original.recipe_id,
            version=original.version,
            expected_current_catalog_sha256=catalog_sha,
            expected_candidate_sha256=candidate.proposed_recipe_sha256,
            confirm_reviewed=False,
        )

    with pytest.raises(SolverPromotionRejected, match="CURRENT_CATALOG_SHA256_MISMATCH"):
        promote_published_recipe(
            path,
            [_food()],
            recipe_id=original.recipe_id,
            version=original.version,
            expected_current_catalog_sha256="0" * 64,
            expected_candidate_sha256=candidate.proposed_recipe_sha256,
            confirm_reviewed=True,
        )

    with pytest.raises(SolverPromotionRejected, match="PROMOTION_CANDIDATE_SHA256_MISMATCH"):
        promote_published_recipe(
            path,
            [_food()],
            recipe_id=original.recipe_id,
            version=original.version,
            expected_current_catalog_sha256=catalog_sha,
            expected_candidate_sha256="0" * 64,
            confirm_reviewed=True,
        )


def test_promotion_atomically_replaces_only_exact_version(tmp_path: Path) -> None:
    path = tmp_path / "recipes.json"
    original = _recipe(_ingredient())
    other = original.model_copy(update={"recipe_id": "recipe-other", "version": "v2", "title": "其他菜谱"})
    _write_catalog(path, [original, other])
    catalog_sha = sha256_file(path)
    candidate = inspect_catalog_promotion(
        path,
        [_food()],
        recipe_id=original.recipe_id,
        version=original.version,
    )

    receipt = promote_published_recipe(
        path,
        [_food()],
        recipe_id=original.recipe_id,
        version=original.version,
        expected_current_catalog_sha256=catalog_sha,
        expected_candidate_sha256=candidate.proposed_recipe_sha256,
        confirm_reviewed=True,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload) == 2
    promoted = next(item for item in payload if item["recipe_id"] == original.recipe_id)
    untouched = next(item for item in payload if item["recipe_id"] == other.recipe_id)
    assert promoted["version"] == candidate.proposed_version
    assert promoted["solver_eligible"] is True
    assert promoted["nutrition_data_version"] == "2026-08"
    assert untouched["version"] == other.version
    assert receipt.previous_catalog_sha256 == catalog_sha
    assert receipt.promoted_catalog_sha256 == sha256_file(path)
    assert receipt.promoted_recipe_sha256 == candidate.proposed_recipe_sha256
