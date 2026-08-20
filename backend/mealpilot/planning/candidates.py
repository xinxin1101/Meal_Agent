from mealpilot.domain.models import (
    CandidateCoverageReport,
    CoverageStatus,
    MealSlot,
    Recipe,
    UserProfile,
)


def filter_safe_candidates(
    recipes: list[Recipe], profile: UserProfile
) -> tuple[list[Recipe], CandidateCoverageReport]:
    """Fail closed before solving: unsafe recipes never reach CP-SAT."""
    allergens = {value.casefold() for value in profile.allergens}
    avoidances = {value.casefold() for value in profile.avoidances}
    accepted: list[Recipe] = []
    exclusions: dict[str, list[str]] = {}

    for recipe in sorted(recipes, key=lambda item: item.recipe_id):
        reasons: list[str] = []
        if not recipe.solver_eligible or recipe.nutrition_per_serving is None:
            reasons.append("NUTRITION_NOT_SOLVER_READY")
        for ingredient in recipe.ingredients:
            terms = {ingredient.canonical_id.casefold(), ingredient.canonical_name.casefold()}
            if allergens and not ingredient.allergen_composition_known:
                reasons.append("UNKNOWN_ALLERGEN_COMPOSITION")
            if allergens.intersection(value.casefold() for value in ingredient.allergens):
                reasons.append("ALLERGEN_MATCH")
            if avoidances.intersection(terms):
                reasons.append("AVOIDANCE_MATCH")
        if reasons:
            exclusions[recipe.recipe_id] = sorted(set(reasons))
        else:
            accepted.append(recipe)

    counts = {slot: sum(slot in recipe.supported_slots for recipe in accepted) for slot in MealSlot}
    if not accepted:
        status = CoverageStatus.EMPTY_RETRIEVAL
    elif any(count == 0 for count in counts.values()):
        status = CoverageStatus.INSUFFICIENT_COVERAGE
    else:
        status = CoverageStatus.READY
    return accepted, CandidateCoverageReport(
        status=status,
        per_slot_count=counts,
        excluded_recipe_ids=sorted(exclusions),
        exclusion_reasons=exclusions,
    )
