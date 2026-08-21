from decimal import Decimal

from mealpilot.domain.models import AdminRecipeCatalogItem, MealSlot, Nutrition, RecipeIngredient, SourceMetadata
from mealpilot.nutrition.catalog import FoodNutrition
from mealpilot.nutrition.readiness import build_solver_readiness_report


def _ingredient(
    canonical_id: str,
    *,
    amount_g: str | None,
    known_allergens: bool = True,
) -> RecipeIngredient:
    return RecipeIngredient(
        canonical_id=canonical_id,
        canonical_name=canonical_id,
        display_quantity=f"{amount_g}g" if amount_g is not None else "适量",
        quantity_kind="MEASURED" if amount_g is not None else "QUALITATIVE",
        amount_g=Decimal(amount_g) if amount_g is not None else None,
        allergens=[],
        allergen_composition_known=known_allergens,
    )


def _item(
    record_id: str,
    *,
    origin: str,
    lifecycle_status: str,
    quality_status: str,
    solver_eligible: bool,
    slots: list[MealSlot],
    ingredients: list[RecipeIngredient],
    blocking: list[str] | None = None,
    solver_blocking: list[str] | None = None,
    processing_stage: str | None = None,
) -> AdminRecipeCatalogItem:
    return AdminRecipeCatalogItem(
        record_id=record_id,
        recipe_id=f"recipe-{record_id}",
        title=record_id,
        origin=origin,
        lifecycle_status=lifecycle_status,
        quality_status=quality_status,
        processing_stage=processing_stage,
        solver_eligible=solver_eligible,
        supported_slots=slots,
        servings=Decimal("1"),
        prep_minutes=20,
        ingredients=ingredients,
        cooking_steps=[],
        source_id="source-1",
        source_url="https://example.org/recipe/1",
        license="internal-personal-study",
        data_version="v1",
        blocking_reasons=blocking or [],
        solver_blocking_reasons=solver_blocking or [],
    )


def _food(canonical_id: str) -> FoodNutrition:
    return FoodNutrition(
        canonical_id=canonical_id,
        food_data_id=f"food-{canonical_id}",
        nutrition_per_100g=Nutrition(
            energy_kcal=Decimal("100"),
            protein_g=Decimal("10"),
            carbohydrate_g=Decimal("10"),
            fat_g=Decimal("2"),
        ),
        source=SourceMetadata(
            source_id="trusted-food-source",
            source_url="https://example.org/foods",
            license="CC-BY-4.0",
            data_version="2026-08",
        ),
    )


def test_report_separates_publication_from_solver_remediation() -> None:
    items = [
        _item(
            "active-breakfast",
            origin="ACTIVE_CATALOG",
            lifecycle_status="ACTIVE",
            quality_status="SOLVER_READY",
            solver_eligible=True,
            slots=[MealSlot.BREAKFAST],
            ingredients=[_ingredient("egg", amount_g="100")],
        ),
        _item(
            "active-display-only",
            origin="ACTIVE_CATALOG",
            lifecycle_status="ACTIVE",
            quality_status="PUBLICATION_READY",
            solver_eligible=False,
            slots=[MealSlot.LUNCH, MealSlot.DINNER],
            ingredients=[
                _ingredient("tofu", amount_g=None),
                _ingredient("mystery-sauce", amount_g="20", known_allergens=False),
            ],
            solver_blocking=[
                "ALLERGEN_COMPOSITION_INCOMPLETE",
                "NUTRITION_COVERAGE_INCOMPLETE",
                "NUTRITION_QUANTITY_INCOMPLETE",
                "NUTRITION_NOT_CALCULABLE",
            ],
        ),
        _item(
            "review-needs-llm",
            origin="REVIEW_QUEUE",
            lifecycle_status="PENDING",
            quality_status="BLOCKED",
            solver_eligible=False,
            slots=[],
            ingredients=[],
            blocking=["SERVINGS_MISSING"],
            solver_blocking=["SERVINGS_MISSING", "NUTRITION_NOT_CALCULABLE"],
            processing_stage="INITIAL_VALIDATED",
        ),
        _item(
            "review-publishable",
            origin="REVIEW_QUEUE",
            lifecycle_status="PENDING",
            quality_status="PUBLICATION_READY",
            solver_eligible=False,
            slots=[MealSlot.DINNER],
            ingredients=[_ingredient("tofu", amount_g="120")],
            solver_blocking=["NUTRITION_COVERAGE_INCOMPLETE", "NUTRITION_NOT_CALCULABLE"],
            processing_stage="FINAL_VALIDATED",
        ),
    ]

    report = build_solver_readiness_report(items, [_food("egg")])

    assert report.published_recipe_count == 2
    assert report.solver_ready_count == 1
    assert report.published_display_only_count == 1
    assert report.pending_review_count == 2
    assert report.publishable_review_count == 1
    assert report.publication_blocked_count == 1
    assert report.per_slot_solver_ready_count == {
        MealSlot.BREAKFAST: 1,
        MealSlot.LUNCH: 0,
        MealSlot.DINNER: 0,
    }
    assert report.strict_planning_ready is False
    assert report.formal_nutrition_record_count == 1
    assert report.formal_nutrition_data_versions == ["2026-08"]
    assert report.missing_formal_nutrition_ids == ["mystery-sauce", "tofu"]

    actions = {(action.record_id, action.scope, action.reason_code): action for action in report.actions}
    assert actions[("review-needs-llm", "PUBLICATION", "LLM_STRUCTURE_PENDING")].llm_assist_allowed is True
    assert actions[("review-needs-llm", "PUBLICATION", "SERVINGS_MISSING")].resolution_kind == "RUN_LLM_STRUCTURE"
    assert actions[("active-display-only", "SOLVER", "NUTRITION_QUANTITY_INCOMPLETE")].resolution_kind == "TRUSTED_QUANTITY_REVIEW"
    assert actions[("active-display-only", "SOLVER", "ALLERGEN_COMPOSITION_INCOMPLETE")].resolution_kind == "TRUSTED_ALLERGEN_REVIEW"
    nutrition_action = actions[("active-display-only", "SOLVER", "NUTRITION_COVERAGE_INCOMPLETE")]
    assert nutrition_action.resolution_kind == "IMPORT_FORMAL_NUTRITION"
    assert nutrition_action.canonical_ids == ["mystery-sauce", "tofu"]
    assert ("active-display-only", "SOLVER", "NUTRITION_NOT_CALCULABLE") not in actions


def test_report_marks_strict_ready_only_with_all_three_solver_slots() -> None:
    ready = _item(
        "active-all-slots",
        origin="ACTIVE_CATALOG",
        lifecycle_status="ACTIVE",
        quality_status="SOLVER_READY",
        solver_eligible=True,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
        ingredients=[_ingredient("egg", amount_g="100")],
    )

    report = build_solver_readiness_report([ready], [_food("egg")])

    assert report.strict_planning_ready is True
    assert report.actions == []
    assert report.missing_formal_nutrition_ids == []
