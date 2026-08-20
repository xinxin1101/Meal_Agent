from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from ortools.sat.python import cp_model

from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import MealPlanningRequest, PlanningFailure, UserProfile
from mealpilot.planning.candidates import filter_safe_candidates
from mealpilot.planning.engine import create_deterministic_plan
from mealpilot.planning.solver import solve_day


RECIPES = load_recipes(Path("data/recipes.sample.json"))


def profile(*, allergens: list[str] | None = None) -> UserProfile:
    return UserProfile(
        profile_snapshot_id="profile-test", adult_confirmed=True, age_years=28,
        nutrition_parameter_sex="unspecified", height_cm=Decimal("170"), weight_kg=Decimal("65"),
        activity_level="moderate", goal="maintain", allergens=allergens or [], avoidances=[],
    )


def request(**overrides: object) -> MealPlanningRequest:
    base: dict[str, object] = {
        "request_id": "request-test", "max_total_minutes": 60,
        "energy_kcal_range": {"min": Decimal("1500"), "max": Decimal("1700")},
        "protein_min_g": Decimal("90"), "numeric_policy_version": "contract-v2-decimal-v1",
    }
    base.update(overrides)
    return MealPlanningRequest.model_validate(base)


def test_fixed_request_produces_valid_deterministic_plan() -> None:
    first = create_deterministic_plan(RECIPES, profile(allergens=["soy"]), request())
    second = create_deterministic_plan(RECIPES, profile(allergens=["soy"]), request())
    assert not isinstance(first, PlanningFailure)
    assert first.validation_report is not None and first.validation_report.valid
    assert first.selections == second.selections
    assert first.totals.protein_g >= Decimal("90")
    assert all(selection.recipe_title for selection in first.selections)
    assert all(selection.ingredients for selection in first.selections)
    assert all(ingredient.display_quantity for selection in first.selections for ingredient in selection.ingredients)


def test_allergen_filter_reports_insufficient_coverage_without_solving() -> None:
    result = create_deterministic_plan(RECIPES, profile(allergens=["egg", "milk"]), request())
    assert isinstance(result, PlanningFailure)
    assert result.status == "INSUFFICIENT_COVERAGE"
    assert result.reason_code == "INSUFFICIENT_MEAL_SLOT_COVERAGE"


def test_time_infeasibility_is_not_reported_as_coverage_failure() -> None:
    result = create_deterministic_plan(RECIPES, profile(), request(max_total_minutes=1))
    assert isinstance(result, PlanningFailure)
    assert result.status == "INFEASIBLE"
    assert result.reason_code == "CONSTRAINTS_INFEASIBLE"


def test_unknown_allergen_composition_is_fail_closed() -> None:
    oat_recipe = next(recipe for recipe in RECIPES if recipe.recipe_id == "recipe-oat-egg-v1")
    unsafe_oat = oat_recipe.model_copy(
        update={"ingredients": [oat_recipe.ingredients[0].model_copy(update={"allergen_composition_known": False})]}
    )
    candidates, report = filter_safe_candidates([unsafe_oat], profile(allergens=["peanut"]))
    assert candidates == []
    assert report.exclusion_reasons[unsafe_oat.recipe_id] == ["UNKNOWN_ALLERGEN_COMPOSITION"]


def test_publication_only_recipe_is_not_a_solver_candidate() -> None:
    oat_recipe = next(recipe for recipe in RECIPES if recipe.recipe_id == "recipe-oat-egg-v1")
    publication_only = oat_recipe.model_copy(update={"solver_eligible": False, "nutrition_per_serving": None, "nutrition_basis": None})
    candidates, report = filter_safe_candidates([publication_only], profile())
    assert candidates == []
    assert report.exclusion_reasons[publication_only.recipe_id] == ["NUTRITION_NOT_SOLVER_READY"]


def test_impossible_energy_range_does_not_create_false_feasible_plan() -> None:
    result = create_deterministic_plan(RECIPES, profile(), request(energy_kcal_range={"min": Decimal("5000"), "max": Decimal("5200")}))
    assert isinstance(result, PlanningFailure)
    assert result.reason_code == "CONSTRAINTS_INFEASIBLE"


def test_solver_unknown_is_not_mislabeled_as_infeasible(monkeypatch: object) -> None:
    class UnknownSolver:
        parameters = SimpleNamespace(max_time_in_seconds=0, num_search_workers=0, random_seed=0)

        def Solve(self, _model: object) -> int:
            return cp_model.UNKNOWN

    monkeypatch.setattr("mealpilot.planning.solver.cp_model.CpSolver", UnknownSolver)
    plan, status = solve_day(RECIPES, request())
    assert plan is None
    assert status == "UNKNOWN"


def test_out_of_range_solver_data_returns_a_structured_failure() -> None:
    oat_recipe = next(recipe for recipe in RECIPES if recipe.recipe_id == "recipe-oat-egg-v1")
    assert oat_recipe.nutrition_per_serving is not None
    oversized = oat_recipe.model_copy(update={"nutrition_per_serving": oat_recipe.nutrition_per_serving.model_copy(update={"energy_kcal": Decimal("999999999999999999")})})
    result = create_deterministic_plan([oversized, *[recipe for recipe in RECIPES if recipe.recipe_id != oversized.recipe_id]], profile(), request())
    assert isinstance(result, PlanningFailure)
    assert result.reason_code == "NUMERIC_RANGE_EXCEEDED"
