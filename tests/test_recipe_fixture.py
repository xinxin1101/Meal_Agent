from pathlib import Path

from mealpilot.data.loader import load_recipes


def test_sample_recipes_are_valid_and_traceable() -> None:
    recipes = load_recipes(Path("data/recipes.sample.json"))
    assert len(recipes) >= 2
    assert all(recipe.source.license for recipe in recipes)
    assert all(recipe.numeric_policy_version for recipe in recipes)
