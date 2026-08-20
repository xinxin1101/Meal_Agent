from decimal import Decimal

import pytest
from pydantic import ValidationError

from mealpilot.domain.contract_migration import migrate_persisted_document, migrate_persisted_proposal, migrate_preference_items
from mealpilot.domain.models import RecipeIngredient, UserProfile


def test_quantitative_and_qualitative_ingredient_contracts() -> None:
    measured = RecipeIngredient.model_validate(
        {
            "canonical_id": "beef",
            "canonical_name": "牛肉",
            "display_quantity": "200克",
            "quantity_kind": "MEASURED",
            "amount_g": "200",
        }
    )
    qualitative = RecipeIngredient.model_validate(
        {
            "canonical_id": "spring-onion",
            "canonical_name": "葱花",
            "display_quantity": "少许",
            "quantity_kind": "QUALITATIVE",
            "amount_g": None,
            "nutrition_calculation_role": "EXCLUDED_MINOR_INGREDIENT",
        }
    )
    assert measured.amount_g == Decimal("200")
    assert qualitative.amount_g is None


def test_qualitative_quantity_cannot_hide_a_measured_mass() -> None:
    with pytest.raises(ValidationError):
        RecipeIngredient.model_validate(
            {
                "canonical_id": "oil",
                "canonical_name": "食用油",
                "display_quantity": "适量",
                "quantity_kind": "QUALITATIVE",
                "amount_g": "10",
            }
        )


def test_persistence_migration_is_not_a_public_api_relaxation() -> None:
    legacy = {
        "profile_snapshot_id": "legacy-profile",
        "adult_confirmed": True,
        "age_years": 28,
        "nutrition_parameter_sex": "unspecified",
        "height_cm": "170",
        "weight_kg": "65",
        "activity_level": "moderate",
        "goal": "maintain",
        "allergens": [],
        "avoidances": [],
        "equipment": ["stovetop"],
    }
    with pytest.raises(ValidationError):
        UserProfile.model_validate(legacy)
    assert UserProfile.model_validate(migrate_persisted_document(legacy)).profile_snapshot_id == "legacy-profile"


def test_retired_memory_categories_are_not_loaded() -> None:
    items = [
        {"category": "food_preference", "value": "清淡"},
        {"category": "budget_habit", "value": "每天四十元"},
        {"category": "equipment", "value": "空气炸锅"},
    ]
    assert migrate_preference_items(items) == [{"category": "food_preference", "value": "清淡"}]


def test_budget_only_legacy_negotiation_is_not_silently_remapped() -> None:
    legacy = {
        "proposal_id": "legacy-budget",
        "reason_code": "BUDGET_INFEASIBLE",
        "options": [{"option_id": "raise-budget", "field": "max_cost_cny", "proposed_value": "50", "impact": "legacy"}],
        "explanation": "legacy",
    }
    assert migrate_persisted_proposal(legacy) is None
