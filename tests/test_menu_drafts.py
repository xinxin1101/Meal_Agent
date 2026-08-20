from pathlib import Path

from fastapi.testclient import TestClient

import mealpilot.main as main
from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import MenuDraftCommand, Recipe
from mealpilot.planning.menu_draft import create_menu_draft
from tests.auth_helpers import authenticated_client


def _profile(*, allergens: list[str] | None = None, avoidances: list[str] | None = None) -> dict:
    return {
        "profile_snapshot_id": "menu-draft-profile",
        "adult_confirmed": True,
        "age_years": 28,
        "nutrition_parameter_sex": "unspecified",
        "height_cm": "170",
        "weight_kg": "65",
        "activity_level": "moderate",
        "goal": "maintain",
        "allergens": allergens or [],
        "avoidances": avoidances or [],
    }


def _display_recipes() -> list[Recipe]:
    values = load_recipes(Path("data/recipes.sample.json"))[:4]
    return [
        recipe.model_copy(update={
            "solver_eligible": False,
            "nutrition_per_serving": None,
            "nutrition_basis": None,
            "nutrition_data_version": None,
            "ingredients": [
                ingredient.model_copy(update={"allergen_composition_known": False})
                for ingredient in recipe.ingredients
            ],
        })
        for recipe in values
    ]


def _command(**changes) -> MenuDraftCommand:
    value = {
        "draft_id": "draft-test",
        "profile": _profile(),
        "max_total_minutes": 60,
        "acknowledge_unverified": True,
    }
    value.update(changes)
    return MenuDraftCommand.model_validate(value)


def test_display_recipes_create_deterministic_unverified_menu_without_nutrition_totals() -> None:
    recipes = _display_recipes()
    first = create_menu_draft(recipes, _command())
    second = create_menu_draft(recipes, _command())
    assert first == second
    assert first.status == "UNVERIFIED_MENU"
    assert len(first.meals) == 3
    assert len({meal.recipe_id for meal in first.meals}) == 3
    assert first.nutrition_complete is False
    assert first.nutrition_totals is None
    assert first.numeric_policy_version == "menu-draft-decimal-v1"
    assert "NUTRITION_TOTALS_HIDDEN_INCOMPLETE" in first.warnings
    assert any(value.startswith("ALLERGEN_COMPOSITION_NOT_FULLY_REVIEWED:") for value in first.warnings)


def test_complete_nutrition_is_summed_but_still_not_presented_as_validated() -> None:
    result = create_menu_draft(load_recipes(Path("data/recipes.sample.json")), _command(max_total_minutes=None))
    assert result.status == "UNVERIFIED_MENU"
    assert result.nutrition_complete is True
    assert result.nutrition_totals is not None
    assert result.nutrition_totals.energy_kcal > 0
    assert result.nutrition_data_versions
    assert "MENU_DRAFT_NOT_NUTRITION_VALIDATED" in result.warnings


def test_declared_allergy_fails_closed_for_unknown_composition() -> None:
    result = create_menu_draft(
        _display_recipes(),
        _command(profile=_profile(allergens=["peanut"])),
    )
    assert result.status == "FAILED"
    assert result.reason_code == "INSUFFICIENT_SAFE_DISPLAY_RECIPES"
    assert result.excluded_recipe_ids
    assert all("UNKNOWN_ALLERGEN_COMPOSITION" in reasons for reasons in result.exclusion_reasons.values())


def test_menu_draft_endpoint_requires_auth_and_returns_display_catalog(monkeypatch) -> None:
    monkeypatch.setattr(main, "_recipes", _display_recipes)
    assert TestClient(main.app).post("/v1/menu-drafts", json={}).status_code == 401
    client, _ = authenticated_client("menu-draft")
    response = client.post("/v1/menu-drafts", json=_command().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["status"] == "UNVERIFIED_MENU"
    assert response.json()["nutrition_totals"] is None
