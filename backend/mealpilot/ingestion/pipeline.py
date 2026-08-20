import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.domain.models import Recipe


class StagedRecipe(BaseModel):
    """Untrusted source data plus deterministic and administrator review state."""
    model_config = ConfigDict(extra="forbid")
    staging_id: str
    captured_at: datetime
    raw_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_status: Literal["APPROVED", "PENDING", "REJECTED"]
    review_status: Literal["APPROVED", "PENDING", "REJECTED"]
    raw_ingredient_text: list[str]
    normalization_warnings: list[str] = Field(default_factory=list)
    source_content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    quality_gate_status: Literal["LEGACY", "PUBLICATION_READY", "SOLVER_READY"] = "LEGACY"
    quality_gate_version: str | None = None
    quality_report_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    authorization_evidence_ids: list[str] = Field(default_factory=list)
    recipe: Recipe


class PublishReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    version: str
    published_at: datetime
    dataset_version: str


class WithdrawalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    version: str
    withdrawn_at: datetime
    dataset_version: str


class DataQualityError(ValueError):
    pass


def recipe_content_hash(recipe: Recipe) -> str:
    canonical = json.dumps(recipe.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_for_publish(staged: StagedRecipe) -> None:
    if staged.license_status != "APPROVED":
        raise DataQualityError("LICENSE_NOT_APPROVED")
    if staged.review_status != "APPROVED":
        raise DataQualityError("REVIEW_NOT_APPROVED")
    if not staged.raw_ingredient_text:
        raise DataQualityError("RAW_INGREDIENTS_MISSING")
    if staged.raw_content_hash != recipe_content_hash(staged.recipe):
        raise DataQualityError("CONTENT_HASH_MISMATCH")
    if not staged.recipe.source.license:
        raise DataQualityError("LICENSE_METADATA_MISSING")
    if staged.recipe.source.source_id.startswith("meishichina:"):
        if staged.quality_gate_status not in {"PUBLICATION_READY", "SOLVER_READY"} or not staged.quality_gate_version or not staged.quality_report_hash:
            raise DataQualityError("QUALITY_GATE_NOT_READY")
        if not staged.recipe.cooking_steps:
            raise DataQualityError("COOKING_STEPS_MISSING")


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def publish(staged: StagedRecipe, published_path: Path, dataset_version: str) -> PublishReceipt:
    """Append a reviewed version; never allow staged data to replace published data silently."""
    validate_for_publish(staged)
    existing = json.loads(published_path.read_text(encoding="utf-8")) if published_path.exists() else []
    if any(item["recipe_id"] == staged.recipe.recipe_id and item["version"] == staged.recipe.version for item in existing):
        raise DataQualityError("RECIPE_VERSION_ALREADY_PUBLISHED")
    existing.append(staged.recipe.model_dump(mode="json"))
    _write_json_atomic(published_path, existing)
    return PublishReceipt(recipe_id=staged.recipe.recipe_id, version=staged.recipe.version, published_at=datetime.now().astimezone(), dataset_version=dataset_version)


def withdraw(recipe_id: str, version: str, published_path: Path, dataset_version: str) -> WithdrawalReceipt:
    """Remove one explicitly identified published version; fail rather than widening scope."""
    if not published_path.exists():
        raise DataQualityError("PUBLISHED_DATASET_MISSING")
    existing = json.loads(published_path.read_text(encoding="utf-8"))
    retained = [item for item in existing if not (item.get("recipe_id") == recipe_id and item.get("version") == version)]
    if len(retained) == len(existing):
        raise DataQualityError("RECIPE_VERSION_NOT_PUBLISHED")
    _write_json_atomic(published_path, retained)
    return WithdrawalReceipt(
        recipe_id=recipe_id,
        version=version,
        withdrawn_at=datetime.now().astimezone(),
        dataset_version=dataset_version,
    )
