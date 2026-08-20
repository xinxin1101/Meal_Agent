import json
from pathlib import Path

from mealpilot.domain.models import Recipe


def load_recipes(path: Path) -> list[Recipe]:
    """Load and validate a local recipe fixture; rejects malformed records."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Recipe.model_validate(item) for item in raw]


def load_recipe_sets(*paths: Path) -> list[Recipe]:
    """Combine existing trusted datasets and reject duplicate recipe versions."""
    recipes: list[Recipe] = []
    versions: set[tuple[str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        for recipe in load_recipes(path):
            key = (recipe.recipe_id, recipe.version)
            if key in versions:
                raise ValueError(f"duplicate recipe version across datasets: {recipe.recipe_id}@{recipe.version}")
            versions.add(key)
            recipes.append(recipe)
    return recipes
