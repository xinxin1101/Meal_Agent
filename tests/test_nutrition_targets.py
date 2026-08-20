from decimal import Decimal

import pytest

from mealpilot.domain.models import UserProfile
from mealpilot.nutrition.targets import suggest_targets


def profile(**changes) -> UserProfile:
    data = {
        "profile_snapshot_id": "nutrition-test", "adult_confirmed": True,
        "age_years": 26, "nutrition_parameter_sex": "male", "height_cm": "182",
        "weight_kg": "68", "activity_level": "light", "goal": "maintain",
        "allergens": [], "avoidances": [],
    }
    data.update(changes)
    return UserProfile.model_validate(data)


def test_target_suggestion_is_versioned_decimal_and_requires_confirmation() -> None:
    suggestion = suggest_targets(profile())
    assert suggestion.policy_version == "healthy-adult-estimate-v1"
    assert suggestion.requires_user_confirmation is True
    assert suggestion.energy_kcal_range.min == Decimal("2211")
    assert suggestion.energy_kcal_range.max == Decimal("2444")
    assert suggestion.protein_min_g == Decimal("56.4")
    assert suggestion.warnings and suggestion.source_references


def test_target_suggestion_goal_changes_only_the_advisory_energy_range() -> None:
    maintain = suggest_targets(profile(goal="maintain"))
    lose = suggest_targets(profile(goal="lose"))
    assert lose.energy_kcal_range.max < maintain.energy_kcal_range.max
    assert lose.protein_min_g == maintain.protein_min_g


def test_unspecified_parameter_fails_closed() -> None:
    with pytest.raises(ValueError, match="female or male"):
        suggest_targets(profile(nutrition_parameter_sex="unspecified"))
