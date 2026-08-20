import json
from pathlib import Path

from mealpilot.nutrition.catalog import FoodNutrition


def load_food_catalog(path: Path) -> list[FoodNutrition]:
    return [FoodNutrition.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
