from pathlib import Path

from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import IngredientQuantityKind
from mealpilot.nutrition.loaders import load_food_catalog
from mealpilot.nutrition.validation import audit_catalog_coverage, calculate_recipe_from_catalog, materialize_catalog_recipes


def test_catalog_recalculation_is_traceable_and_warns_on_declared_difference() -> None:
    recipe = next(item for item in load_recipes(Path("data/recipes.sample.json")) if item.recipe_id == "recipe-oat-egg-v1")
    metrics = calculate_recipe_from_catalog(recipe, load_food_catalog(Path("data/nutrition/foods.sample.json")))
    assert metrics.nutrition_per_serving is not None
    assert metrics.nutrition_data_version == "2026-08"
    assert "DECLARED_ENERGY_DIFFERS_FROM_CATALOG" in metrics.warnings


def test_catalog_recalculation_does_not_claim_totals_when_coverage_is_missing() -> None:
    recipe = next(item for item in load_recipes(Path("data/recipes.sample.json")) if item.recipe_id == "recipe-chicken-rice-v1")
    foods = [item for item in load_food_catalog(Path("data/nutrition/foods.sample.json")) if item.canonical_id != "brown-rice"]
    metrics = calculate_recipe_from_catalog(recipe, foods)
    assert metrics.nutrition_per_serving is None
    assert metrics.missing_nutrition_ids == ["brown-rice"]


def test_qualitative_included_ingredient_is_not_given_an_invented_mass() -> None:
    recipe = load_recipes(Path("data/recipes.sample.json"))[0]
    qualitative = recipe.ingredients[0].model_copy(update={
        "display_quantity": "适量",
        "quantity_kind": IngredientQuantityKind.QUALITATIVE,
        "amount_g": None,
    })
    recipe = recipe.model_copy(update={"ingredients": [qualitative, *recipe.ingredients[1:]]})
    metrics = calculate_recipe_from_catalog(recipe, load_food_catalog(Path("data/nutrition/foods.sample.json")))
    assert metrics.nutrition_per_serving is None
    assert metrics.unresolved_quantity_ids == [qualitative.canonical_id]


def test_catalog_coverage_gate_passes_for_the_pinned_development_snapshot() -> None:
    report = audit_catalog_coverage(
        load_recipes(Path("data/recipes.sample.json")),
        load_food_catalog(Path("data/nutrition/foods.sample.json")),
    )
    assert report.complete is True
    assert report.recipe_count == 5
    assert report.solver_eligible_count == 5
    assert report.missing_nutrition_by_recipe == {}
    assert report.unresolved_quantity_by_recipe == {}


def test_catalog_coverage_gate_reports_a_deliberately_removed_mapping() -> None:
    recipes = load_recipes(Path("data/recipes.sample.json"))
    foods = [item for item in load_food_catalog(Path("data/nutrition/foods.sample.json")) if item.canonical_id != "broccoli"]
    report = audit_catalog_coverage(recipes, foods)
    assert report.complete is False
    assert report.missing_nutrition_by_recipe == {"recipe-beef-vegetables-v1": ["broccoli"]}


def test_materialized_recipes_use_catalog_values_and_preserve_nutrition_evidence() -> None:
    recipes = load_recipes(Path("data/recipes.sample.json"))
    materialized = materialize_catalog_recipes(recipes, load_food_catalog(Path("data/nutrition/foods.sample.json")))
    oat = next(item for item in materialized if item.recipe_id == "recipe-oat-egg-v1")
    declared = next(item for item in recipes if item.recipe_id == oat.recipe_id)
    assert oat.nutrition_data_version == "2026-08"
    assert oat.nutrition_per_serving is not None
    assert declared.nutrition_per_serving is not None
    assert oat.nutrition_per_serving.energy_kcal != declared.nutrition_per_serving.energy_kcal
