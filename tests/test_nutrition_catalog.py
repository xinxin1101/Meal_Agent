from decimal import Decimal

from mealpilot.nutrition.catalog import IngredientConversion, normalize_quantity


def test_normalization_requires_density_or_portion_weight() -> None:
    egg = IngredientConversion(canonical_id="egg", portion_g=Decimal("50"), data_version="v1")
    assert normalize_quantity("egg", Decimal("2"), "piece", egg).amount_g == Decimal("100")
    assert normalize_quantity("egg", Decimal("100"), "ml", egg).warning == "DENSITY_MISSING"
    assert normalize_quantity("unknown", Decimal("1"), "cup", None).warning == "UNIT_UNSUPPORTED"
