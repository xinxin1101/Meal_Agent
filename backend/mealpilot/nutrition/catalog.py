from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from mealpilot.domain.models import Nutrition, SourceMetadata


class FoodNutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    food_data_id: str
    nutrition_per_100g: Nutrition
    source: SourceMetadata


class IngredientConversion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    portion_g: Decimal | None = Field(default=None, gt=0)
    data_version: str


class NormalizedQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    amount_g: Decimal | None
    warning: str | None
    conversion_source: str | None


def normalize_quantity(canonical_id: str, amount: Decimal, unit: str, conversion: IngredientConversion | None) -> NormalizedQuantity:
    normalized = unit.casefold().strip()
    if normalized == "g":
        return NormalizedQuantity(canonical_id=canonical_id, amount_g=amount, warning=None, conversion_source="mass:g")
    if normalized == "kg":
        return NormalizedQuantity(canonical_id=canonical_id, amount_g=amount * Decimal("1000"), warning=None, conversion_source="mass:kg")
    if normalized == "oz":
        return NormalizedQuantity(canonical_id=canonical_id, amount_g=amount * Decimal("28.349523125"), warning=None, conversion_source="mass:oz")
    if normalized == "ml":
        if conversion is None or conversion.density_g_per_ml is None:
            return NormalizedQuantity(canonical_id=canonical_id, amount_g=None, warning="DENSITY_MISSING", conversion_source=None)
        return NormalizedQuantity(canonical_id=canonical_id, amount_g=amount * conversion.density_g_per_ml, warning=None, conversion_source=f"density:{conversion.data_version}")
    if normalized in {"count", "piece"}:
        if conversion is None or conversion.portion_g is None:
            return NormalizedQuantity(canonical_id=canonical_id, amount_g=None, warning="PORTION_WEIGHT_MISSING", conversion_source=None)
        return NormalizedQuantity(canonical_id=canonical_id, amount_g=amount * conversion.portion_g, warning=None, conversion_source=f"portion:{conversion.data_version}")
    return NormalizedQuantity(canonical_id=canonical_id, amount_g=None, warning="UNIT_UNSUPPORTED", conversion_source=None)
