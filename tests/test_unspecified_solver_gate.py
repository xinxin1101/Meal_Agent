from decimal import Decimal

from mealpilot.domain.models import (
    IngredientQuantityKind,
    Nutrition,
    QuantityOrigin,
    Recipe,
    RecipeIngredient,
    SourceMetadata,
)
from mealpilot.nutrition.validation import calculate_recipe_from_catalog


def test_source_declared_nutrition_does_not_bypass_unspecified_quantity_gate() -> None:
    recipe = Recipe(
        recipe_id="display-only-source-declared",
        version="1",
        title="来源营养但数量缺失",
        supported_slots=["lunch"],
        servings=Decimal("1"),
        prep_minutes=10,
        nutrition_per_serving=Nutrition(
            energy_kcal=Decimal("500"),
            protein_g=Decimal("30"),
            carbohydrate_g=Decimal("50"),
            fat_g=Decimal("15"),
        ),
        nutrition_basis="SOURCE_DECLARED",
        solver_eligible=False,
        ingredients=[
            RecipeIngredient(
                canonical_id="beef",
                canonical_name="牛肉",
                display_quantity="用量未注明",
                quantity_kind=IngredientQuantityKind.UNSPECIFIED,
                quantity_origin=QuantityOrigin.DISPLAY_FALLBACK,
                amount_g=None,
                allergens=[],
            )
        ],
        source=SourceMetadata(
            source_id="test",
            source_url="https://example.com/source-declared",
            license="test",
            data_version="v1",
        ),
        numeric_policy_version="test",
        nutrition_data_version="source-v1",
    )

    metrics = calculate_recipe_from_catalog(recipe, [])
    assert metrics.nutrition_per_serving is None
    assert metrics.unresolved_quantity_ids == ["beef"]
    assert metrics.warnings == ["NUTRITION_QUANTITY_INCOMPLETE"]
