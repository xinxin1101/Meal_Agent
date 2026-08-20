"""Version-pinned deterministic evaluation for the frozen local MVP dataset."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import MealPlanningRequest, PlanningFailure, UserProfile
from mealpilot.nutrition.loaders import load_food_catalog
from mealpilot.nutrition.validation import materialize_catalog_recipes
from mealpilot.planning.engine import create_deterministic_plan


class EvaluationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    profile: UserProfile
    request: MealPlanningRequest
    expected: Literal["PLAN", "FAILURE"]
    expected_reason_code: str | None = None


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    passed: bool
    observed: str


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_version: str
    recipe_data_version: str
    nutrition_data_version: str | None
    numeric_policy_versions: list[str]
    cases: list[EvaluationCaseResult]


def evaluate_scenarios(project_root: Path) -> EvaluationReport:
    raw_scenarios = json.loads((project_root / "data" / "evaluation" / "scenarios.v2.json").read_text(encoding="utf-8"))
    scenarios = [EvaluationScenario.model_validate(item) for item in raw_scenarios]
    recipes = load_recipes(project_root / "data" / "recipes.sample.json")
    foods = load_food_catalog(project_root / "data" / "nutrition" / "foods.sample.json")
    planning_recipes = materialize_catalog_recipes(recipes, foods)
    cases: list[EvaluationCaseResult] = []
    for scenario in scenarios:
        result = create_deterministic_plan(planning_recipes, scenario.profile, scenario.request)
        if scenario.expected == "PLAN":
            passed = not isinstance(result, PlanningFailure)
            observed = "PLAN" if passed else result.reason_code
        else:
            observed = result.reason_code if isinstance(result, PlanningFailure) else "PLAN"
            passed = observed == scenario.expected_reason_code
        cases.append(EvaluationCaseResult(scenario_id=scenario.scenario_id, passed=passed, observed=observed))
    return EvaluationReport(
        evaluation_version="mvp-scenarios-v2",
        recipe_data_version=recipes[0].source.data_version,
        nutrition_data_version=planning_recipes[0].nutrition_data_version,
        numeric_policy_versions=sorted({scenario.request.numeric_policy_version for scenario in scenarios}),
        cases=cases,
    )
