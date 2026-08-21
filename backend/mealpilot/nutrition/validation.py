"""Nutrition-only catalog materialization for the price-free v2 contract."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from mealpilot.domain.models import IngredientQuantityKind, Nutrition, Recipe
from mealpilot.nutrition.catalog import FoodNutrition


class CatalogRecipeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nutrition_per_serving: Nutrition | None
    missing_nutrition_ids: list[str]
    unresolved_quantity_ids: list[str]
    nutrition_data_version: str | None
    warnings: list[str]


class CatalogCoverageReport(BaseModel):
    """Read-only nutrition gate for the recipes that declare Solver eligibility."""

    model_config = ConfigDict(extra="forbid")
    complete: bool
    recipe_count: int
    solver_eligible_count: int
    nutrition_data_version: str | None
    missing_nutrition_by_recipe: dict[str, list[str]]
    unresolved_quantity_by_recipe: dict[str, list[str]]
    publication_only_recipe_ids: list[str]


def calculate_recipe_from_catalog(recipe: Recipe, foods: list[FoodNutrition]) -> CatalogRecipeMetrics:
    """Calculate nutrition only from measured ingredients; never invent qualitative or unspecified grams."""

    included = [item for item in recipe.ingredients if item.nutrition_calculation_role == "INCLUDED"]
    unspecified_quantity = sorted({
        item.canonical_id for item in included if item.quantity_kind == IngredientQuantityKind.UNSPECIFIED
    })
    if unspecified_quantity:
        return CatalogRecipeMetrics(
            nutrition_per_serving=None,
            missing_nutrition_ids=[],
            unresolved_quantity_ids=unspecified_quantity,
            nutrition_data_version=None,
            warnings=["NUTRITION_QUANTITY_INCOMPLETE"],
        )

    if recipe.nutrition_basis in {"SOURCE_DECLARED", "REVIEWED_STANDARD_PORTION"} and recipe.nutrition_per_serving is not None:
        return CatalogRecipeMetrics(
            nutrition_per_serving=recipe.nutrition_per_serving,
            missing_nutrition_ids=[],
            unresolved_quantity_ids=[],
            nutrition_data_version=recipe.nutrition_data_version,
            warnings=[],
        )

    catalog = {food.canonical_id: food for food in foods}
    missing_nutrition = sorted({item.canonical_id for item in included if item.canonical_id not in catalog})
    unresolved_quantity = sorted({item.canonical_id for item in included if item.amount_g is None})
    warnings: list[str] = []
    nutrition: Nutrition | None = None
    version: str | None = None

    if missing_nutrition:
        warnings.append("NUTRITION_COVERAGE_INCOMPLETE")
    if unresolved_quantity:
        warnings.append("NUTRITION_QUANTITY_INCOMPLETE")
    if not missing_nutrition and not unresolved_quantity:
        total = {
            "energy_kcal": Decimal("0"),
            "protein_g": Decimal("0"),
            "carbohydrate_g": Decimal("0"),
            "fat_g": Decimal("0"),
        }
        versions: set[str] = set()
        for ingredient in included:
            assert ingredient.amount_g is not None
            food = catalog[ingredient.canonical_id]
            versions.add(food.source.data_version)
            for key in total:
                total[key] += getattr(food.nutrition_per_100g, key) * ingredient.amount_g / Decimal("100")
        nutrition = Nutrition(**{key: value / recipe.servings for key, value in total.items()})
        version = versions.pop() if len(versions) == 1 else ("mixed" if versions else None)
        if recipe.nutrition_per_serving is not None and abs(
            nutrition.energy_kcal - recipe.nutrition_per_serving.energy_kcal
        ) > Decimal("5"):
            warnings.append("DECLARED_ENERGY_DIFFERS_FROM_CATALOG")

    return CatalogRecipeMetrics(
        nutrition_per_serving=nutrition,
        missing_nutrition_ids=missing_nutrition,
        unresolved_quantity_ids=unresolved_quantity,
        nutrition_data_version=version,
        warnings=warnings,
    )


def materialize_catalog_recipes(recipes: list[Recipe], foods: list[FoodNutrition]) -> list[Recipe]:
    """Materialize eligible recipes and retain publication-only recipes for explicit filtering."""

    materialized: list[Recipe] = []
    for recipe in recipes:
        if not recipe.solver_eligible:
            materialized.append(recipe)
            continue
        metrics = calculate_recipe_from_catalog(recipe, foods)
        if metrics.nutrition_per_serving is None:
            materialized.append(recipe.model_copy(update={"solver_eligible": False}))
            continue
        materialized.append(
            recipe.model_copy(
                update={
                    "nutrition_per_serving": metrics.nutrition_per_serving,
                    "nutrition_basis": recipe.nutrition_basis or "CALCULATED_FROM_INGREDIENTS",
                    "nutrition_data_version": metrics.nutrition_data_version,
                }
            )
        )
    return materialized


def audit_catalog_coverage(recipes: list[Recipe], foods: list[FoodNutrition]) -> CatalogCoverageReport:
    """Report nutrition gaps without treating publication-only recipes as trusted Solver inputs."""

    missing_by_recipe: dict[str, list[str]] = {}
    unresolved_by_recipe: dict[str, list[str]] = {}
    publication_only: list[str] = []
    for recipe in sorted(recipes, key=lambda item: item.recipe_id):
        if not recipe.solver_eligible:
            publication_only.append(recipe.recipe_id)
            continue
        metrics = calculate_recipe_from_catalog(recipe, foods)
        if metrics.missing_nutrition_ids:
            missing_by_recipe[recipe.recipe_id] = metrics.missing_nutrition_ids
        if metrics.unresolved_quantity_ids:
            unresolved_by_recipe[recipe.recipe_id] = metrics.unresolved_quantity_ids
    versions = {food.source.data_version for food in foods}
    return CatalogCoverageReport(
        complete=not missing_by_recipe and not unresolved_by_recipe,
        recipe_count=len(recipes),
        solver_eligible_count=sum(item.solver_eligible for item in recipes),
        nutrition_data_version=versions.pop() if len(versions) == 1 else ("mixed" if versions else None),
        missing_nutrition_by_recipe=missing_by_recipe,
        unresolved_quantity_by_recipe=unresolved_by_recipe,
        publication_only_recipe_ids=publication_only,
    )
