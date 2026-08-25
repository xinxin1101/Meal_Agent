"""Administrator-facing recipe review contracts and trusted curation builder."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mealpilot.domain.models import MealSlot, QuantityOrigin
from mealpilot.ingestion.quality import (
    IngredientOverride,
    LEXICON,
    RecipeCuration,
    normalize_name,
    resolve_ingredient_identity,
)
from mealpilot.ingestion.review import RecipeReviewItem
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe


class CanonicalIngredientOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    canonical_name: str
    allergens: list[str]
    allergen_composition_known: bool


class AdminIngredientPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: int = Field(ge=0)
    raw_name: str = Field(min_length=1, max_length=200)
    canonical_id: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=20)
    qualitative_label: Literal["适量", "少许"] | None = None
    nutrition_calculation_role: Literal["INCLUDED", "EXCLUDED_MINOR_INGREDIENT"] = "INCLUDED"

    @model_validator(mode="after")
    def quantity_pair(self) -> "AdminIngredientPatch":
        if (self.amount is None) != (self.unit is None):
            raise ValueError("amount and unit must be supplied together")
        if self.amount is not None and self.qualitative_label is not None:
            raise ValueError("measured and qualitative quantities are mutually exclusive")
        return self


class AdminReviewCurationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_review_version: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    servings: Decimal | None = Field(default=None, gt=0, le=100)
    supported_slots: list[MealSlot] = Field(default_factory=list, max_length=3)
    prep_minutes: int | None = Field(default=None, ge=0, le=1440)
    ingredients: list[AdminIngredientPatch]
    step_overrides: dict[int, str] = Field(default_factory=dict)
    excluded_step_numbers: list[int] = Field(default_factory=list)


class AdminReviewVersionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_review_version: int = Field(ge=0)


class AdminPublishCommand(AdminReviewVersionCommand):
    dataset_version: str = Field(min_length=1, max_length=100)


class AdminRevokeCommand(AdminReviewVersionCommand):
    reason: str = Field(min_length=1, max_length=500)
    dataset_version: str = Field(min_length=1, max_length=100)


class AdminReviewDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review: RecipeReviewItem
    canonical_ingredients: list[CanonicalIngredientOption]
    can_approve: bool
    can_publish_readable: bool
    can_publish: bool


class AdminBatchReviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    expected_review_version: int = Field(ge=0)


class AdminBatchPublishCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AdminBatchReviewTarget] = Field(min_length=1, max_length=50)
    dataset_version: str = Field(min_length=1, max_length=100)
    confirm_internal_personal_study: Literal[True]


class AdminBatchPublishResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str
    status: Literal["PUBLISHED", "READABLE_PUBLISHED", "FAILED"]
    review_version: int | None = None
    reason_code: str | None = None


class AdminBatchPublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    published_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    results: list[AdminBatchPublishResult]


def canonical_options(raw: RawMeishiChinaRecipe | None = None) -> list[CanonicalIngredientOption]:
    by_id: dict[str, CanonicalIngredientOption] = {}
    ambiguous: set[str] = set()
    for entry in LEXICON.values():
        option = CanonicalIngredientOption(
            canonical_id=entry.canonical_id, canonical_name=entry.canonical_name,
            allergens=list(entry.allergens), allergen_composition_known=entry.composition_known,
        )
        previous = by_id.get(entry.canonical_id)
        if previous is not None and previous != option:
            ambiguous.add(entry.canonical_id)
        else:
            by_id[entry.canonical_id] = option
    if raw is not None:
        for ingredient in raw.ingredients:
            entry = resolve_ingredient_identity(ingredient.raw_name)
            by_id.setdefault(
                entry.canonical_id,
                CanonicalIngredientOption(
                    canonical_id=entry.canonical_id,
                    canonical_name=entry.canonical_name,
                    allergens=list(entry.allergens),
                    allergen_composition_known=entry.composition_known,
                ),
            )
    return [by_id[key] for key in sorted(by_id) if key not in ambiguous]


def build_trusted_curation(raw: RawMeishiChinaRecipe, command: AdminReviewCurationCommand) -> RecipeCuration:
    options = {item.canonical_id: item for item in canonical_options(raw)}
    seen: set[int] = set()
    overrides: list[IngredientOverride] = []
    for patch in command.ingredients:
        if patch.source_index >= len(raw.ingredients):
            raise ValueError(f"ingredient source index is not present: {patch.source_index}")
        source = raw.ingredients[patch.source_index]
        if normalize_name(source.raw_name) != normalize_name(patch.raw_name):
            raise ValueError(f"ingredient name does not match source index: {patch.source_index}")
        if patch.source_index in seen:
            raise ValueError(f"duplicate ingredient patch index: {patch.source_index}")
        seen.add(patch.source_index)
        if patch.canonical_id is None:
            continue
        option = options.get(patch.canonical_id)
        if option is None:
            raise ValueError(f"canonical ingredient is not trusted: {patch.canonical_id}")
        source_label = source.raw_amount.strip()
        label = patch.qualitative_label
        if label is None and patch.amount is None and source_label in {"适量", "少许"}:
            label = source_label  # type: ignore[assignment]
        quantity_origin = QuantityOrigin.SOURCE_EXPLICIT
        if patch.amount is not None or (patch.qualitative_label is not None and patch.qualitative_label != source_label):
            quantity_origin = QuantityOrigin.REVIEWER_CONFIRMED
        overrides.append(IngredientOverride(
            source_index=patch.source_index, raw_name=source.raw_name, canonical_id=option.canonical_id,
            canonical_name=option.canonical_name, amount=patch.amount, unit=patch.unit,
            qualitative_label=label, quantity_origin=quantity_origin,
            nutrition_calculation_role=patch.nutrition_calculation_role,
            allergens=option.allergens,
            allergen_composition_known=option.allergen_composition_known,
        ))
    unknown_step_numbers = set(command.step_overrides).union(command.excluded_step_numbers) - {item.step_number for item in raw.cooking_steps}
    if unknown_step_numbers:
        raise ValueError(f"unknown cooking step numbers: {sorted(unknown_step_numbers)}")
    return RecipeCuration(
        title_override=command.title.strip() if command.title else None,
        servings=command.servings, supported_slots=command.supported_slots,
        prep_minutes=command.prep_minutes, ingredient_overrides=overrides,
        step_overrides=command.step_overrides,
        excluded_step_numbers=command.excluded_step_numbers,
    )
