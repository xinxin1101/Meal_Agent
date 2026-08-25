"""Deterministic non-nutrition menu drafts from published display recipes."""

from __future__ import annotations

from decimal import Decimal

from mealpilot.domain.models import (
    MealSlot,
    MenuDraft,
    MenuDraftCommand,
    MenuDraftFailure,
    MenuDraftMeal,
    Nutrition,
    Recipe,
)


def _safe_display_recipes(
    recipes: list[Recipe], command: MenuDraftCommand
) -> tuple[list[Recipe], dict[str, list[str]]]:
    allergens = {value.casefold() for value in command.profile.allergens}
    avoidances = {value.casefold() for value in command.profile.avoidances}
    accepted: list[Recipe] = []
    excluded: dict[str, list[str]] = {}
    for recipe in sorted(recipes, key=lambda item: item.recipe_id):
        reasons: list[str] = []
        for ingredient in recipe.ingredients:
            terms = {ingredient.canonical_id.casefold(), ingredient.canonical_name.casefold()}
            if avoidances.intersection(terms):
                reasons.append("AVOIDANCE_MATCH")
            if allergens and not ingredient.allergen_composition_known:
                reasons.append("UNKNOWN_ALLERGEN_COMPOSITION")
            if allergens.intersection(value.casefold() for value in ingredient.allergens):
                reasons.append("ALLERGEN_MATCH")
        if reasons:
            excluded[recipe.recipe_id] = sorted(set(reasons))
        else:
            accepted.append(recipe)
    return accepted, excluded


def _select_distinct_slots(recipes: list[Recipe]) -> list[tuple[MealSlot, Recipe]] | None:
    """Find a deterministic three-slot matching; never relabel another meal."""

    slots = (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER)

    def assign(index: int, remaining: list[Recipe], selected: list[tuple[MealSlot, Recipe]]) -> list[tuple[MealSlot, Recipe]] | None:
        if index == len(slots):
            return selected
        slot = slots[index]
        for recipe in sorted((item for item in remaining if slot in item.supported_slots), key=lambda item: (item.prep_minutes, item.recipe_id)):
            result = assign(index + 1, [item for item in remaining if item.recipe_id != recipe.recipe_id], [*selected, (slot, recipe)])
            if result is not None:
                return result
        return None

    return assign(0, recipes, [])


def create_menu_draft(recipes: list[Recipe], command: MenuDraftCommand) -> MenuDraft | MenuDraftFailure:
    """Create a transparent cooking draft without pretending nutrition was validated."""

    if not recipes:
        return MenuDraftFailure(
            reason_code="NO_PUBLISHED_RECIPES",
            message="The published recipe catalog is empty.",
        )
    candidates, exclusions = _safe_display_recipes(recipes, command)
    if len(candidates) < 3:
        return MenuDraftFailure(
            reason_code="INSUFFICIENT_SAFE_DISPLAY_RECIPES",
            message="Fewer than three published recipes remain after explicit allergy and avoidance filtering.",
            excluded_recipe_ids=sorted(exclusions),
            exclusion_reasons=exclusions,
        )
    selected_slots = _select_distinct_slots(candidates)
    if selected_slots is None:
        return MenuDraftFailure(
            reason_code="INSUFFICIENT_MEAL_SLOT_COVERAGE",
            message="Published display recipes do not cover three distinct meal slots.",
            excluded_recipe_ids=sorted(exclusions),
            exclusion_reasons=exclusions,
        )

    meals: list[MenuDraftMeal] = []
    warnings = [
        "MENU_DRAFT_NOT_NUTRITION_VALIDATED",
        "MENU_DRAFT_NOT_MEDICAL_ADVICE",
    ]
    for slot, recipe in selected_slots:
        meal_warnings: list[str] = []
        if any(not ingredient.allergen_composition_known for ingredient in recipe.ingredients):
            meal_warnings.append("ALLERGEN_COMPOSITION_NOT_FULLY_REVIEWED")
            warnings.append(f"ALLERGEN_COMPOSITION_NOT_FULLY_REVIEWED:{recipe.recipe_id}")
        if recipe.nutrition_per_serving is None:
            meal_warnings.append("NUTRITION_DATA_UNAVAILABLE")
        meals.append(MenuDraftMeal(
            slot=slot,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            recipe_title=recipe.title,
            ingredients=recipe.ingredients,
            cooking_steps=recipe.cooking_steps,
            prep_minutes=recipe.prep_minutes,
            nutrition_per_serving=recipe.nutrition_per_serving,
            source=recipe.source,
            warnings=meal_warnings,
        ))

    total_minutes = sum(meal.prep_minutes for meal in meals)
    if command.max_total_minutes is not None and total_minutes > command.max_total_minutes:
        warnings.append("TIME_PREFERENCE_EXCEEDED")
    nutrition_complete = all(meal.nutrition_per_serving is not None for meal in meals)
    nutrition_totals = None
    if nutrition_complete:
        totals = {
            "energy_kcal": Decimal("0"),
            "protein_g": Decimal("0"),
            "carbohydrate_g": Decimal("0"),
            "fat_g": Decimal("0"),
        }
        for meal in meals:
            assert meal.nutrition_per_serving is not None
            for field in totals:
                totals[field] += getattr(meal.nutrition_per_serving, field)
        nutrition_totals = Nutrition(**totals)
    else:
        warnings.append("NUTRITION_TOTALS_HIDDEN_INCOMPLETE")

    return MenuDraft(
        draft_id=command.draft_id,
        meals=meals,
        total_prep_minutes=total_minutes,
        nutrition_totals=nutrition_totals,
        nutrition_complete=nutrition_complete,
        warnings=list(dict.fromkeys(warnings)),
        nutrition_data_versions=sorted({
            recipe.nutrition_data_version
            or f"unversioned:{recipe.recipe_id}@{recipe.version}:{recipe.nutrition_basis or 'UNKNOWN'}"
            for recipe in candidates
            if recipe.recipe_id in {meal.recipe_id for meal in meals} and recipe.nutrition_per_serving is not None
        }),
    )
