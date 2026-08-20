"""Validation and atomic installation for the formal nutrition catalog."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.nutrition.catalog import FoodNutrition
from mealpilot.nutrition.loaders import load_food_catalog


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FORBIDDEN_FORMAL_LICENSES = {"internal-development-only"}


class FormalNutritionCatalogRejected(ValueError):
    """Raised when a candidate cannot cross the formal nutrition trust boundary."""


class FormalNutritionCatalogReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_count: int = Field(gt=0)
    canonical_ids: list[str]
    source_ids: list[str]
    data_versions: list[str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_clean_identifier(value: str, reason_code: str) -> None:
    if not value or value != value.strip():
        raise FormalNutritionCatalogRejected(reason_code)


def inspect_formal_catalog(
    path: Path,
    *,
    expected_sha256: str | None = None,
) -> tuple[list[FoodNutrition], FormalNutritionCatalogReport]:
    """Validate a candidate without mutating runtime state."""

    if not path.is_file():
        raise FormalNutritionCatalogRejected("FORMAL_NUTRITION_CANDIDATE_NOT_FOUND")

    content_sha256 = sha256_file(path)
    if expected_sha256 is not None:
        expected = expected_sha256.strip().casefold()
        if not _SHA256.fullmatch(expected):
            raise FormalNutritionCatalogRejected("EXPECTED_SHA256_INVALID")
        if content_sha256 != expected:
            raise FormalNutritionCatalogRejected("CANDIDATE_SHA256_MISMATCH")

    try:
        foods = load_food_catalog(path)
    except Exception as error:
        raise FormalNutritionCatalogRejected("FORMAL_NUTRITION_SCHEMA_INVALID") from error

    if not foods:
        raise FormalNutritionCatalogRejected("FORMAL_NUTRITION_CATALOG_EMPTY")

    for food in foods:
        _require_clean_identifier(food.canonical_id, "CANONICAL_ID_INVALID")
        _require_clean_identifier(food.food_data_id, "FOOD_DATA_ID_INVALID")
        _require_clean_identifier(food.source.source_id, "SOURCE_ID_INVALID")
        _require_clean_identifier(food.source.license, "SOURCE_LICENSE_INVALID")
        _require_clean_identifier(food.source.data_version, "SOURCE_DATA_VERSION_INVALID")
        if food.source.license.casefold() in _FORBIDDEN_FORMAL_LICENSES:
            raise FormalNutritionCatalogRejected("TEST_ONLY_LICENSE_NOT_FORMAL")
        if ".invalid" in str(food.source.source_url).casefold():
            raise FormalNutritionCatalogRejected("TEST_ONLY_SOURCE_URL_NOT_FORMAL")

    canonical_ids = [food.canonical_id for food in foods]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise FormalNutritionCatalogRejected("DUPLICATE_CANONICAL_ID")

    food_data_ids = [food.food_data_id for food in foods]
    if len(food_data_ids) != len(set(food_data_ids)):
        raise FormalNutritionCatalogRejected("DUPLICATE_FOOD_DATA_ID")

    return foods, FormalNutritionCatalogReport(
        content_sha256=content_sha256,
        record_count=len(foods),
        canonical_ids=sorted(canonical_ids),
        source_ids=sorted({food.source.source_id for food in foods}),
        data_versions=sorted({food.source.data_version for food in foods}),
    )


def install_formal_catalog(
    candidate_path: Path,
    destination_path: Path,
    *,
    expected_candidate_sha256: str,
    confirm_reviewed: bool,
    expected_current_sha256: str | None = None,
) -> FormalNutritionCatalogReport:
    """Install exact reviewed bytes atomically after hash and OCC checks."""

    if not confirm_reviewed:
        raise FormalNutritionCatalogRejected("REVIEW_CONFIRMATION_REQUIRED")

    _, report = inspect_formal_catalog(candidate_path, expected_sha256=expected_candidate_sha256)

    if destination_path.exists():
        if not destination_path.is_file():
            raise FormalNutritionCatalogRejected("FORMAL_NUTRITION_DESTINATION_INVALID")
        if expected_current_sha256 is None:
            raise FormalNutritionCatalogRejected("CURRENT_SHA256_REQUIRED_FOR_REPLACEMENT")
        expected_current = expected_current_sha256.strip().casefold()
        if not _SHA256.fullmatch(expected_current):
            raise FormalNutritionCatalogRejected("EXPECTED_CURRENT_SHA256_INVALID")
        if sha256_file(destination_path) != expected_current:
            raise FormalNutritionCatalogRejected("CURRENT_SHA256_MISMATCH")
    elif expected_current_sha256 is not None:
        raise FormalNutritionCatalogRejected("CURRENT_CATALOG_NOT_FOUND")

    payload = candidate_path.read_bytes()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(f".{destination_path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination_path)
    finally:
        temporary.unlink(missing_ok=True)

    if sha256_file(destination_path) != report.content_sha256:
        raise FormalNutritionCatalogRejected("INSTALLED_SHA256_MISMATCH")
    return report
