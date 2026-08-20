from decimal import Decimal

from mealpilot.domain.models import (
    MealPlan,
    MealPlanningRequest,
    PlanTotals,
    Recipe,
    UserProfile,
    ValidationReport,
)


def validate_plan(
    plan: MealPlan | None,
    recipes_by_id: dict[str, Recipe],
    profile: UserProfile,
    request: MealPlanningRequest,
) -> ValidationReport:
    """Recalculate from Decimal source values; solver output is never authoritative."""
    if plan is None:
        return ValidationReport(valid=False, violations=["PLAN_MISSING"], warnings=[], totals=None, validator_version=request.numeric_policy_version, nutrition_data_version=None)

    totals = PlanTotals(
        energy_kcal=Decimal("0"), protein_g=Decimal("0"), carbohydrate_g=Decimal("0"),
        fat_g=Decimal("0"), prep_minutes=0,
    )
    violations: list[str] = []
    allergens = {value.casefold() for value in profile.allergens}
    avoidances = {value.casefold() for value in profile.avoidances}
    seen_slots = set()
    for selection in plan.selections:
        seen_slots.add(selection.slot)
        recipe = recipes_by_id.get(selection.recipe_id)
        if recipe is None:
            violations.append("RECIPE_NOT_FOUND")
            continue
        if not recipe.solver_eligible or recipe.nutrition_per_serving is None:
            violations.append("NUTRITION_NOT_SOLVER_READY")
            continue
        portion = Decimal(selection.portion)
        totals.energy_kcal += recipe.nutrition_per_serving.energy_kcal * portion
        totals.protein_g += recipe.nutrition_per_serving.protein_g * portion
        totals.carbohydrate_g += recipe.nutrition_per_serving.carbohydrate_g * portion
        totals.fat_g += recipe.nutrition_per_serving.fat_g * portion
        totals.prep_minutes += recipe.prep_minutes
        for ingredient in recipe.ingredients:
            if allergens and not ingredient.allergen_composition_known:
                violations.append("UNKNOWN_ALLERGEN_COMPOSITION")
            if allergens.intersection(value.casefold() for value in ingredient.allergens):
                violations.append("ALLERGEN_MATCH")
            if avoidances.intersection({ingredient.canonical_id.casefold(), ingredient.canonical_name.casefold()}):
                violations.append("AVOIDANCE_MATCH")

    if len(seen_slots) != 3:
        violations.append("MEAL_SLOT_COUNT_INVALID")
    if not request.energy_kcal_range.min <= totals.energy_kcal <= request.energy_kcal_range.max:
        violations.append("ENERGY_RANGE_VIOLATION")
    if totals.protein_g < request.protein_min_g:
        violations.append("PROTEIN_MIN_VIOLATION")
    if totals.prep_minutes > request.max_total_minutes:
        violations.append("TIME_BUDGET_VIOLATION")
    return ValidationReport(
        valid=not violations, violations=sorted(set(violations)), warnings=[], totals=totals,
        validator_version=request.numeric_policy_version,
        nutrition_data_version=_common_nutrition_version(recipes_by_id.values()),
    )


def _common_nutrition_version(recipes: object) -> str | None:
    versions = {recipe.nutrition_data_version for recipe in recipes if recipe.nutrition_data_version is not None}
    return versions.pop() if len(versions) == 1 else ("mixed" if versions else None)
