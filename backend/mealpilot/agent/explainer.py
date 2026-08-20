"""Explanation Agent: may describe only a plan already validated by deterministic code."""

from mealpilot.domain.models import MealPlan
from mealpilot.llm.siliconflow import explain_verified_plan


def explain_plan(plan: MealPlan, recipe_titles: dict[str, str]) -> str | None:
    meals = "; ".join(
        f"{selection.slot}: {recipe_titles.get(selection.recipe_id, selection.recipe_id)} x {selection.portion}"
        for selection in plan.selections
    )
    totals = plan.totals
    summary = (
        f"Verified meals: {meals}. Verified totals: energy {totals.energy_kcal} kcal, "
        f"protein {totals.protein_g} g, time {totals.prep_minutes} minutes."
    )
    return explain_verified_plan(summary)
