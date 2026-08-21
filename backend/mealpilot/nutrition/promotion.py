"""Deterministic promotion of published display recipes into Solver-ready versions."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import Recipe
from mealpilot.ingestion.pipeline import recipe_content_hash
from mealpilot.nutrition.catalog import FoodNutrition
from mealpilot.nutrition.validation import calculate_recipe_from_catalog


_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class SolverPromotionRejected(ValueError):
    pass


class SolverPromotionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    previous_version: str
    proposed_version: str
    previous_recipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposed_recipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    nutrition_data_version: str
    recipe: Recipe


class SolverPromotionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    previous_version: str
    promoted_version: str
    previous_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    promoted_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    promoted_recipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    nutrition_data_version: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _promotion_version(recipe: Recipe, nutrition_data_version: str) -> str:
    basis = f"{recipe_content_hash(recipe)}:{nutrition_data_version}:solver-promotion-v1"
    suffix = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"{recipe.version}-sr-{suffix}"


def build_solver_promotion_candidate(
    recipe: Recipe,
    foods: list[FoodNutrition],
) -> SolverPromotionCandidate:
    """Build a promotion only when all Solver trust requirements are already satisfied."""

    if recipe.solver_eligible:
        raise SolverPromotionRejected("RECIPE_ALREADY_SOLVER_READY")
    if any(not ingredient.allergen_composition_known for ingredient in recipe.ingredients):
        raise SolverPromotionRejected("ALLERGEN_COMPOSITION_INCOMPLETE")

    metrics = calculate_recipe_from_catalog(recipe, foods)
    if metrics.missing_nutrition_ids:
        raise SolverPromotionRejected("NUTRITION_COVERAGE_INCOMPLETE")
    if metrics.unresolved_quantity_ids:
        raise SolverPromotionRejected("NUTRITION_QUANTITY_INCOMPLETE")
    if metrics.nutrition_per_serving is None or metrics.nutrition_data_version is None:
        raise SolverPromotionRejected("NUTRITION_NOT_CALCULABLE")

    promoted = recipe.model_copy(
        update={
            "version": _promotion_version(recipe, metrics.nutrition_data_version),
            "nutrition_per_serving": metrics.nutrition_per_serving,
            "nutrition_basis": recipe.nutrition_basis or "CALCULATED_FROM_INGREDIENTS",
            "nutrition_data_version": metrics.nutrition_data_version,
            "solver_eligible": True,
        }
    )
    return SolverPromotionCandidate(
        recipe_id=recipe.recipe_id,
        previous_version=recipe.version,
        proposed_version=promoted.version,
        previous_recipe_sha256=recipe_content_hash(recipe),
        proposed_recipe_sha256=recipe_content_hash(promoted),
        nutrition_data_version=metrics.nutrition_data_version,
        recipe=promoted,
    )


def inspect_catalog_promotion(
    published_path: Path,
    foods: list[FoodNutrition],
    *,
    recipe_id: str,
    version: str,
) -> SolverPromotionCandidate:
    if not published_path.is_file():
        raise SolverPromotionRejected("PUBLISHED_CATALOG_NOT_FOUND")
    recipes = load_recipes(published_path)
    matches = [recipe for recipe in recipes if recipe.recipe_id == recipe_id and recipe.version == version]
    if not matches:
        raise SolverPromotionRejected("RECIPE_VERSION_NOT_PUBLISHED")
    if len(matches) != 1:
        raise SolverPromotionRejected("DUPLICATE_RECIPE_VERSION")
    return build_solver_promotion_candidate(matches[0], foods)


def promote_published_recipe(
    published_path: Path,
    foods: list[FoodNutrition],
    *,
    recipe_id: str,
    version: str,
    expected_current_catalog_sha256: str,
    expected_candidate_sha256: str,
    confirm_reviewed: bool,
) -> SolverPromotionReceipt:
    """Atomically replace one reviewed display-only version with its Solver-ready successor."""

    if not confirm_reviewed:
        raise SolverPromotionRejected("REVIEW_CONFIRMATION_REQUIRED")
    if not published_path.is_file():
        raise SolverPromotionRejected("PUBLISHED_CATALOG_NOT_FOUND")

    expected_catalog = expected_current_catalog_sha256.strip().casefold()
    expected_candidate = expected_candidate_sha256.strip().casefold()
    if not _SHA256.fullmatch(expected_catalog):
        raise SolverPromotionRejected("EXPECTED_CURRENT_CATALOG_SHA256_INVALID")
    if not _SHA256.fullmatch(expected_candidate):
        raise SolverPromotionRejected("EXPECTED_CANDIDATE_SHA256_INVALID")

    current_catalog_sha = sha256_file(published_path)
    if current_catalog_sha != expected_catalog:
        raise SolverPromotionRejected("CURRENT_CATALOG_SHA256_MISMATCH")

    candidate = inspect_catalog_promotion(
        published_path,
        foods,
        recipe_id=recipe_id,
        version=version,
    )
    if candidate.proposed_recipe_sha256 != expected_candidate:
        raise SolverPromotionRejected("PROMOTION_CANDIDATE_SHA256_MISMATCH")

    raw = json.loads(published_path.read_text(encoding="utf-8"))
    if any(
        item.get("recipe_id") == candidate.recipe_id and item.get("version") == candidate.proposed_version
        for item in raw
    ):
        raise SolverPromotionRejected("PROMOTED_VERSION_ALREADY_EXISTS")

    replaced = 0
    next_records: list[object] = []
    for item in raw:
        if item.get("recipe_id") == recipe_id and item.get("version") == version:
            next_records.append(candidate.recipe.model_dump(mode="json"))
            replaced += 1
        else:
            next_records.append(item)
    if replaced != 1:
        raise SolverPromotionRejected("RECIPE_VERSION_NOT_UNIQUE")

    published_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = published_path.with_name(f".{published_path.name}.{uuid4().hex}.tmp")
    payload = (json.dumps(next_records, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Parse the exact candidate catalog after durable file close and before it becomes visible to readers.
        load_recipes(temporary)
        os.replace(temporary, published_path)
    finally:
        temporary.unlink(missing_ok=True)

    installed = load_recipes(published_path)
    promoted = [
        recipe for recipe in installed
        if recipe.recipe_id == candidate.recipe_id and recipe.version == candidate.proposed_version
    ]
    if len(promoted) != 1 or not promoted[0].solver_eligible:
        raise SolverPromotionRejected("PROMOTED_CATALOG_VERIFICATION_FAILED")

    return SolverPromotionReceipt(
        recipe_id=candidate.recipe_id,
        previous_version=candidate.previous_version,
        promoted_version=candidate.proposed_version,
        previous_catalog_sha256=current_catalog_sha,
        promoted_catalog_sha256=sha256_file(published_path),
        promoted_recipe_sha256=candidate.proposed_recipe_sha256,
        nutrition_data_version=candidate.nutrition_data_version,
    )
