from datetime import datetime, timezone
from pathlib import Path

import pytest

from mealpilot.data.loader import load_recipes
from mealpilot.ingestion.pipeline import DataQualityError, StagedRecipe, publish, recipe_content_hash


def staged_recipe(**overrides: object) -> StagedRecipe:
    recipe = load_recipes(Path("data/recipes.sample.json"))[0]
    payload: dict[str, object] = {
        "staging_id": "stage-001", "captured_at": datetime(2026, 8, 6, tzinfo=timezone.utc),
        "raw_content_hash": recipe_content_hash(recipe), "license_status": "APPROVED", "review_status": "APPROVED",
        "raw_ingredient_text": ["60g oats", "2 eggs"], "recipe": recipe,
    }
    payload.update(overrides)
    return StagedRecipe.model_validate(payload)


def test_only_reviewed_and_licensed_record_can_publish(tmp_path: Path) -> None:
    receipt = publish(staged_recipe(), tmp_path / "published.json", "dataset-v1")
    assert receipt.recipe_id == "recipe-oat-egg-v1"
    with pytest.raises(DataQualityError, match="RECIPE_VERSION_ALREADY_PUBLISHED"):
        publish(staged_recipe(), tmp_path / "published.json", "dataset-v1")


def test_hash_and_license_quality_gates_block_publication(tmp_path: Path) -> None:
    with pytest.raises(DataQualityError, match="CONTENT_HASH_MISMATCH"):
        publish(staged_recipe(raw_content_hash="0" * 64), tmp_path / "published.json", "dataset-v1")
    with pytest.raises(DataQualityError, match="LICENSE_NOT_APPROVED"):
        publish(staged_recipe(license_status="PENDING"), tmp_path / "published.json", "dataset-v1")
