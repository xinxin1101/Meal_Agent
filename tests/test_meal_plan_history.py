from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from mealpilot.domain.models import AdoptedMealPlan
from mealpilot.history.store import HistoryConflict, MealPlanHistoryStore
from mealpilot.planning.solver import solve_day
from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import MealPlanningRequest


def history_plan(user_id: str = "history-user", suffix: str = "1") -> AdoptedMealPlan:
    return AdoptedMealPlan.model_validate({
        "history_id": f"history-{suffix}", "history_version": 1, "user_id": user_id,
        "original_run_id": f"run-{suffix}", "original_plan_id": f"plan-{suffix}",
        "adopted_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
        "meals": [
            {"slot": "breakfast", "recipe_id": "recipe-oat-egg-v1", "recipe_version": "1", "recipe_title": "燕麦鸡蛋碗", "portion": "1.0"},
            {"slot": "lunch", "recipe_id": "recipe-chicken-rice-v1", "recipe_version": "1", "recipe_title": "鸡胸肉糙米饭", "portion": "1.0"},
            {"slot": "dinner", "recipe_id": "recipe-beef-vegetables-v1", "recipe_version": "1", "recipe_title": "牛肉时蔬", "portion": "1.0"},
        ],
        "verified_totals": {"energy_kcal": "1590", "protein_g": "112.5", "carbohydrate_g": "148", "fat_g": "57", "prep_minutes": 59},
        "planning_constraints": {"max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90"},
        "profile_snapshot_id": "profile-history", "validation_report": {"valid": True, "violations": [], "warnings": [], "totals": {"energy_kcal": "1590", "protein_g": "112.5", "carbohydrate_g": "148", "fat_g": "57", "prep_minutes": 59}, "validator_version": "m2-decimal-1", "nutrition_data_version": "nutrition-v1"},
        "recipe_data_version": "2026-08-06", "nutrition_data_version": "nutrition-v1", "numeric_policy_version": "contract-v2-decimal-v1", "status": "ADOPTED",
    })


def test_history_store_is_idempotent_versioned_and_hard_deletes(tmp_path: Path) -> None:
    store = MealPlanHistoryStore(tmp_path / "history.sqlite3")
    adopted = store.adopt(history_plan(), "adopt-key")
    replay = store.adopt(history_plan(suffix="different"), "adopt-key")
    assert replay.history_id == adopted.history_id
    collection = store.collection("history-user")
    assert collection.collection_version == 1 and len(collection.items) == 1
    deleted = store.delete("history-user", adopted.history_id, collection.collection_version, "delete-key")
    assert deleted.collection_version == 2 and deleted.items == []
    with pytest.raises(KeyError):
        store.get("history-user", adopted.history_id)
    with pytest.raises(HistoryConflict):
        store.adopt(history_plan(), "adopt-key")
    with pytest.raises(HistoryConflict):
        store.clear("history-user", expected_version=1, idempotency_key="stale-clear")


def test_history_is_only_a_lower_priority_deterministic_solver_objective() -> None:
    recipes = load_recipes(Path("data/recipes.sample.json"))
    request = MealPlanningRequest.model_validate({"request_id": "history-objective", "max_total_minutes": 70, "energy_kcal_range": {"min": Decimal("1400"), "max": Decimal("1800")}, "protein_min_g": Decimal("70"), "numeric_policy_version": "contract-v2-decimal-v1"})
    first, _ = solve_day(recipes, request)
    assert first is not None
    counts = {selection.recipe_id: 10 for selection in first.selections}
    second, _ = solve_day(recipes, request, counts)
    assert second is not None
    assert second.validation_report is None  # Solver still requires authoritative post-validation.
    assert {item.slot for item in second.selections} == {item.slot for item in first.selections}
