from pathlib import Path

import pytest

from mealpilot.history.store import HistoryConflict, MealPlanHistoryStore
from mealpilot.domain.models import SavePlanFeedbackCommand
from tests.test_meal_plan_history import history_plan


def command(version: int = 0) -> SavePlanFeedbackCommand:
    return SavePlanFeedbackCommand.model_validate({
        "expected_feedback_version": version,
        "meals": [
            {"slot": "breakfast", "outcome": "COMPLETED", "rating": 5},
            {"slot": "lunch", "outcome": "SKIPPED", "rating": 2},
            {"slot": "dinner", "outcome": "REPLACED", "replacement_recipe_id": "recipe-tofu-noodles-v1"},
        ],
        "directives": [
            {"recipe_id": "recipe-oat-egg-v1", "action": "REUSE"},
            {"recipe_id": "recipe-beef-vegetables-v1", "action": "AVOID"},
        ],
    })


def test_feedback_is_explicit_idempotent_versioned_and_deleted_with_history(tmp_path: Path) -> None:
    store = MealPlanHistoryStore(tmp_path / "history.sqlite3")
    plan = store.adopt(history_plan(), "adopt")
    feedback = store.save_feedback(plan.user_id, plan.history_id, command(), "feedback-key")
    assert feedback.feedback_version == 1
    assert store.save_feedback(plan.user_id, plan.history_id, command(), "feedback-key") == feedback
    with pytest.raises(HistoryConflict):
        store.save_feedback(plan.user_id, plan.history_id, command(), "different-key")

    scores = store.explicit_recipe_scores(plan.user_id)
    assert scores["recipe-oat-egg-v1"] < 0
    assert scores["recipe-beef-vegetables-v1"] > 0
    assert scores["recipe-chicken-rice-v1"] > 0
    assert scores["recipe-tofu-noodles-v1"] < 0

    collection = store.collection(plan.user_id)
    store.delete(plan.user_id, plan.history_id, collection.collection_version, "delete")
    assert store.feedback_collection(plan.user_id).items == []
    assert store.explicit_recipe_scores(plan.user_id) == {}


def test_feedback_rejects_cross_user_and_requires_replacement_id(tmp_path: Path) -> None:
    store = MealPlanHistoryStore(tmp_path / "history.sqlite3")
    plan = store.adopt(history_plan(), "adopt")
    with pytest.raises(KeyError):
        store.save_feedback("another-user", plan.history_id, command(), "cross-user")
    with pytest.raises(ValueError):
        SavePlanFeedbackCommand.model_validate({
            "expected_feedback_version": 0,
            "meals": [{"slot": "dinner", "outcome": "REPLACED"}],
        })


def test_no_feedback_produces_no_soft_sorting_signal(tmp_path: Path) -> None:
    store = MealPlanHistoryStore(tmp_path / "history.sqlite3")
    store.adopt(history_plan(), "adopt")
    assert store.explicit_recipe_scores("history-user") == {}
