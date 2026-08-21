"""MC-R3 deterministic normalization and recipe publication quality gates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mealpilot.domain.models import CookingStep, IngredientQuantityKind, MealSlot, Nutrition, QuantityOrigin
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe, RawRecipeIngredient
from mealpilot.ingestion.sources.meishichina.parser import IMAGE_DEPENDENT, MEDICAL_CLAIM
from mealpilot.nutrition.catalog import FoodNutrition, IngredientConversion, normalize_quantity


QUALITY_GATE_VERSION = "mc-r3-v4"
QUALITATIVE_AMOUNTS = {"适量", "少许"}
DISPLAYABLE_AMBIGUOUS_AMOUNTS = {"若干", "酌量", "按需", "随意"}
PRESENTATION_ONLY_STEP = re.compile(
    r"^(?:成品(?:图)?|完成图|装盘图|效果图|早餐|午餐|晚餐)[\s。.!！]*$",
    re.IGNORECASE,
)
SOURCE_NOTE_STEP = re.compile(r"^(?:购买的.+|.+第一次购买.+)[。.!！]*$", re.IGNORECASE)
ACTION_WORD = re.compile(
    r"(?:洗|切|放|加|倒|煮|炒|蒸|烤|拌|煎|炸|炖|焯|腌|盛|装盘|沥|搅|撕|剥|去|泡|焖|熬|压|擀|揉|撒|淋|翻|准备|处理|调|"
    r"抓|静置|备用|斩|解冻|开背|盖|焗|抖|取|捞|下入|擦|铺|分开|出锅|备菜|破|剖)"
)
AMOUNT_PATTERN = re.compile(
    r"^([零〇一二两三四五六七八九十百\d.]+)\s*(克|g|千克|公斤|kg|毫升|ml|个|只|根|块|片|斤|两)(?:左右)?$",
    re.IGNORECASE,
)
EMBEDDED_MASS_PATTERN = re.compile(
    r"[（(]\s*([零〇一二两三四五六七八九十百\d.]+)\s*(克|g|千克|公斤|kg|毫升|ml)\s*[）)]",
    re.IGNORECASE,
)


class IngredientOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_name: str = Field(min_length=1)
    canonical_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    amount: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1)
    qualitative_label: Literal["适量", "少许"] | None = None
    quantity_origin: QuantityOrigin = QuantityOrigin.SOURCE_EXPLICIT
    nutrition_calculation_role: Literal["INCLUDED", "EXCLUDED_MINOR_INGREDIENT"] = "INCLUDED"
    allergens: list[str]
    allergen_composition_known: bool
    density_g_per_ml: Decimal | None = Field(default=None, gt=0)
    portion_g: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def amount_and_unit_are_paired(self) -> "IngredientOverride":
        if (self.amount is None) != (self.unit is None):
            raise ValueError("amount and unit must be supplied together")
        if self.amount is not None and self.qualitative_label is not None:
            raise ValueError("measured and qualitative quantities are mutually exclusive")
        if self.nutrition_calculation_role == "EXCLUDED_MINOR_INGREDIENT" and self.qualitative_label is None:
            raise ValueError("only a qualitative ingredient may be excluded as minor")
        return self


class RecipeCuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_override: str | None = Field(default=None, min_length=1, max_length=120)
    servings: Decimal | None = Field(default=None, gt=0)
    supported_slots: list[MealSlot] = Field(default_factory=list)
    prep_minutes: int | None = Field(default=None, ge=0)
    nutrition_per_serving_override: Nutrition | None = None
    nutrition_basis: Literal["SOURCE_DECLARED", "REVIEWED_STANDARD_PORTION"] | None = None
    nutrition_data_version: str | None = None
    ingredient_overrides: list[IngredientOverride] = Field(default_factory=list)
    step_overrides: dict[int, str] = Field(default_factory=dict)
    excluded_step_numbers: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_overrides(self) -> "RecipeCuration":
        names = [item.raw_name for item in self.ingredient_overrides]
        if len(names) != len(set(names)):
            raise ValueError("ingredient overrides must have unique raw_name values")
        if any(number < 1 for number in self.step_overrides) or any(number < 1 for number in self.excluded_step_numbers):
            raise ValueError("step numbers must be positive")
        if set(self.step_overrides).intersection(self.excluded_step_numbers):
            raise ValueError("a step cannot be both overridden and excluded")
        nutrition_values = (self.nutrition_per_serving_override, self.nutrition_basis, self.nutrition_data_version)
        if any(value is not None for value in nutrition_values) and not all(value is not None for value in nutrition_values):
            raise ValueError("nutrition override, basis, and data version must be supplied together")
        return self


class NormalizedDraftIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: Literal["main", "secondary", "seasoning", "other"]
    raw_name: str
    raw_amount: str
    canonical_id: str | None
    canonical_name: str | None
    display_quantity: str
    quantity_kind: IngredientQuantityKind | None
    quantity_origin: QuantityOrigin = QuantityOrigin.SOURCE_EXPLICIT
    amount_g: Decimal | None = Field(default=None, gt=0)
    nutrition_calculation_role: Literal["INCLUDED", "EXCLUDED_MINOR_INGREDIENT"]
    allergens: list[str] = Field(default_factory=list)
    allergen_composition_known: bool
    conversion_source: str | None
    blocking_reasons: list[str] = Field(default_factory=list)


class StructuredRecipeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staging_id: str
    source_id: str
    source_url: str
    source_content_hash: str
    recipe_id: str
    version: str
    title: str
    supported_slots: list[MealSlot]
    servings: Decimal | None = Field(default=None, gt=0)
    prep_minutes: int | None = Field(default=None, ge=0)
    ingredients: list[NormalizedDraftIngredient]
    cooking_steps: list[CookingStep]
    nutrition_per_serving: Nutrition | None
    nutrition_basis: Literal["CALCULATED_FROM_INGREDIENTS", "SOURCE_DECLARED", "REVIEWED_STANDARD_PORTION"] | None
    solver_eligible: bool
    nutrition_data_version: str | None
    numeric_policy_version: Literal["mc-r3-v2", "mc-r3-v3", "mc-r3-v4"] = QUALITY_GATE_VERSION
    removed_medical_text_count: int = Field(ge=0)
    removed_presentation_step_numbers: list[int] = Field(default_factory=list)


class RecipeQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staging_id: str
    quality_gate_version: Literal["mc-r3-v2", "mc-r3-v3", "mc-r3-v4"] = QUALITY_GATE_VERSION
    status: Literal["BLOCKED", "PUBLICATION_READY", "SOLVER_READY"]
    blocking_reasons: list[str]
    solver_blocking_reasons: list[str]
    warnings: list[str]
    unresolved_ingredient_names: list[str]
    unresolved_quantity_names: list[str]
    unspecified_quantity_names: list[str] = Field(default_factory=list)
    unknown_allergen_composition_names: list[str]
    missing_nutrition_ids: list[str]
    image_independent_steps: bool
    servings_resolved: bool
    time_resolved: bool
    slots_resolved: bool
    nutrition_complete: bool
    solver_eligible: bool
    report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class LexiconEntry:
    canonical_id: str
    canonical_name: str
    allergens: tuple[str, ...] = ()
    composition_known: bool = True


LEXICON: dict[str, LexiconEntry] = {
    "鸡蛋": LexiconEntry("egg", "鸡蛋", ("egg",)),
    "燕麦": LexiconEntry("oats", "燕麦", ("gluten",)),
    "鸡胸": LexiconEntry("chicken-breast", "鸡胸肉"),
    "鸡胸肉": LexiconEntry("chicken-breast", "鸡胸肉"),
    "糙米": LexiconEntry("brown-rice", "糙米"),
    "酸奶": LexiconEntry("yogurt", "酸奶", ("milk",)),
    "牛肉": LexiconEntry("beef", "牛肉"),
    "西兰花": LexiconEntry("broccoli", "西兰花"),
    "西蓝花": LexiconEntry("broccoli", "西兰花"),
    "豆腐": LexiconEntry("tofu", "豆腐", ("soy",)),
    "面条": LexiconEntry("noodles", "面条", ("gluten",)),
    "水": LexiconEntry("water", "水"),
    "土豆": LexiconEntry("potato", "土豆"),
    "红薯": LexiconEntry("sweet-potato", "红薯"),
    "苦瓜": LexiconEntry("bitter-melon", "苦瓜"),
    "菠菜": LexiconEntry("spinach", "菠菜"),
    "秋葵": LexiconEntry("okra", "秋葵"),
    "洋葱": LexiconEntry("onion", "洋葱"),
    "生姜": LexiconEntry("ginger", "生姜"),
    "蒜": LexiconEntry("garlic", "蒜"),
    "蒜头": LexiconEntry("garlic", "蒜"),
    "蒜片": LexiconEntry("garlic", "蒜"),
    "蒜蓉": LexiconEntry("garlic", "蒜"),
    "葱花": LexiconEntry("spring-onion", "葱"),
    "葱绿": LexiconEntry("spring-onion", "葱"),
    "三文鱼": LexiconEntry("salmon", "三文鱼", ("fish",)),
    "马头鱼": LexiconEntry("horsehead-fish", "马头鱼", ("fish",)),
    "黑魚片": LexiconEntry("snakehead-fish", "黑鱼", ("fish",)),
    "小鱿鱼": LexiconEntry("squid", "鱿鱼", ("mollusc",)),
    "虾": LexiconEntry("shrimp", "虾", ("crustacean",)),
    "螃蟹": LexiconEntry("crab", "螃蟹", ("crustacean",)),
    "腊肉": LexiconEntry("cured-pork", "腊肉", (), False),
    "香肠": LexiconEntry("sausage", "香肠", (), False),
    "鸡爪": LexiconEntry("chicken-feet", "鸡爪"),
    "食用油": LexiconEntry("cooking-oil", "食用油"),
    "食油": LexiconEntry("cooking-oil", "食用油"),
    "油": LexiconEntry("cooking-oil", "食用油"),
    "花生油": LexiconEntry("peanut-oil", "花生油", ("peanut",)),
    "盐": LexiconEntry("salt", "盐"),
    "白砂糖": LexiconEntry("sugar", "白砂糖"),
    "陈醋": LexiconEntry("vinegar", "陈醋"),
    "生抽": LexiconEntry("soy-sauce", "生抽", ("soy", "gluten"), False),
    "料酒": LexiconEntry("cooking-wine", "料酒", (), False),
    "蚝油": LexiconEntry("oyster-sauce", "蚝油", ("mollusc",), False),
    "面粉": LexiconEntry("wheat-flour", "面粉", ("gluten",)),
    "白芝麻": LexiconEntry("sesame", "白芝麻", ("sesame",)),
    "普宁黄豆酱": LexiconEntry("soybean-paste", "黄豆酱", ("soy",), False),
    "金汤酸酱": LexiconEntry("sour-soup-sauce", "金汤酸酱", (), False),
    "火锅底料": LexiconEntry("hotpot-base", "火锅底料", (), False),
    "蒸肉粉": LexiconEntry("steaming-rice-flour-mix", "蒸肉粉", (), False),
    "鸡精": LexiconEntry("chicken-seasoning", "鸡精", (), False),
    "辣椒芝麻油": LexiconEntry("chili-sesame-oil", "辣椒芝麻油", ("sesame",), False),
    "淀粉水": LexiconEntry("starch-slurry", "淀粉水", (), False),
    "干辣椒": LexiconEntry("dried-chili", "干辣椒"),
    "干红辣椒": LexiconEntry("dried-chili", "干辣椒"),
    "小米椒": LexiconEntry("xiaomi-chili", "小米椒"),
    "小米辣": LexiconEntry("xiaomi-chili", "小米椒"),
    "辣椒": LexiconEntry("chili-pepper", "辣椒"),
    "海椒面": LexiconEntry("chili-powder", "辣椒粉"),
    "红辣椒": LexiconEntry("red-chili", "红辣椒"),
    "青椒": LexiconEntry("green-pepper", "青椒"),
    "花椒": LexiconEntry("sichuan-pepper", "花椒"),
    "花椒粒": LexiconEntry("sichuan-pepper", "花椒"),
    "花椒面": LexiconEntry("sichuan-pepper", "花椒"),
    "麻椒粒": LexiconEntry("sichuan-pepper", "花椒"),
}

DEFAULT_CONVERSIONS = {
    "egg": IngredientConversion(canonical_id="egg", portion_g=Decimal("50"), data_version=QUALITY_GATE_VERSION),
    "water": IngredientConversion(canonical_id="water", density_g_per_ml=Decimal("1"), data_version=QUALITY_GATE_VERSION),
    "cooking-oil": IngredientConversion(canonical_id="cooking-oil", density_g_per_ml=Decimal("0.92"), data_version=QUALITY_GATE_VERSION),
    "peanut-oil": IngredientConversion(canonical_id="peanut-oil", density_g_per_ml=Decimal("0.92"), data_version=QUALITY_GATE_VERSION),
    "vinegar": IngredientConversion(canonical_id="vinegar", density_g_per_ml=Decimal("1.01"), data_version=QUALITY_GATE_VERSION),
}

TIME_LABELS = {"十分钟": 10, "廿分钟": 20, "半小时": 30, "三刻钟": 45, "一小时": 60}


def normalize_name(value: str) -> str:
    return re.sub(r"[\s　]+", "", value).strip().casefold()


def resolve_ingredient_identity(raw_name: str) -> LexiconEntry:
    """Resolve identity without requiring a manual raw-name mapping.

    Known aliases still collapse to a reviewed identity. Any other source name
    becomes its own stable identity, but its allergen composition remains
    unknown until local trusted facts are added. Identity, allergen safety and
    Solver nutrition coverage therefore remain separate concerns.
    """
    normalized = normalize_name(raw_name)
    entry = LEXICON.get(normalized)
    if entry is not None:
        return entry
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return LexiconEntry(
        canonical_id=f"raw-{digest}",
        canonical_name=raw_name.strip(),
        allergens=(),
        composition_known=False,
    )


def chinese_number(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        pass
    simple = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in simple:
        return Decimal(simple[value])
    if value == "十":
        return Decimal(10)
    if "十" in value and all(character in simple or character == "十" for character in value):
        left, _, right = value.partition("十")
        tens = simple.get(left, 1) if left else 1
        ones = simple.get(right, 0) if right else 0
        return Decimal(tens * 10 + ones)
    return None


def parse_amount(raw_amount: str) -> tuple[Decimal | None, str | None, str | None]:
    cleaned = clean_amount(raw_amount)
    if cleaned in QUALITATIVE_AMOUNTS:
        return None, None, None
    if not cleaned or cleaned in DISPLAYABLE_AMBIGUOUS_AMOUNTS:
        return None, None, "AMOUNT_UNSPECIFIED"
    embedded = EMBEDDED_MASS_PATTERN.search(cleaned)
    match = embedded or AMOUNT_PATTERN.fullmatch(cleaned)
    if not match:
        return None, None, "AMOUNT_FORMAT_UNSUPPORTED"
    amount = chinese_number(match.group(1))
    if amount is None or amount <= 0:
        return None, None, "AMOUNT_VALUE_INVALID"
    unit = match.group(2).casefold()
    unit_map = {"克": "g", "g": "g", "千克": "kg", "公斤": "kg", "kg": "kg", "毫升": "ml", "ml": "ml", "个": "count", "只": "count", "根": "count", "块": "count", "片": "count"}
    if unit == "斤":
        return amount * Decimal("500"), "g", None
    if unit == "两":
        return amount * Decimal("50"), "g", None
    return amount, unit_map[unit], None


def clean_amount(value: str) -> str:
    # The source occasionally contains stable OCR/input variants. Correct only
    # spelling, never a missing amount or a portion-to-gram conversion.
    return re.sub(r"\s+", "", value).strip().casefold().replace("亳升", "毫升").replace("左古", "左右")


def _is_ingredient_label_step(raw: RawMeishiChinaRecipe, instruction: str) -> bool:
    """Drop a step that is only a list of ingredients already present in source data."""

    source_names = {normalize_name(item.raw_name) for item in raw.ingredients}
    tokens = [normalize_name(value) for value in re.split(r"[、，,。.!！]+", instruction) if value.strip()]
    return bool(tokens) and all(token in source_names for token in tokens)


def resolve_quantity(
    ingredient: RawRecipeIngredient,
    canonical_id: str,
    override: IngredientOverride | None,
) -> tuple[Decimal | None, str | None, str | None]:
    if override is not None and override.amount is not None and override.unit is not None:
        amount, unit, warning = override.amount, override.unit.casefold(), None
    else:
        amount, unit, warning = parse_amount(ingredient.raw_amount)
    if warning or amount is None or unit is None:
        return None, None, warning or "AMOUNT_UNRESOLVED"
    if override is not None and (override.density_g_per_ml is not None or override.portion_g is not None):
        conversion = IngredientConversion(
            canonical_id=canonical_id,
            density_g_per_ml=override.density_g_per_ml,
            portion_g=override.portion_g,
            data_version=QUALITY_GATE_VERSION,
        )
    else:
        conversion = DEFAULT_CONVERSIONS.get(canonical_id)
    result = normalize_quantity(canonical_id, amount, unit, conversion)
    return result.amount_g, result.conversion_source, result.warning


def normalize_ingredient(ingredient: RawRecipeIngredient, override: IngredientOverride | None) -> NormalizedDraftIngredient:
    entry = resolve_ingredient_identity(ingredient.raw_name)
    if override is not None:
        canonical_id, canonical_name = override.canonical_id, override.canonical_name
        allergens, known = override.allergens, override.allergen_composition_known
    else:
        canonical_id, canonical_name = entry.canonical_id, entry.canonical_name
        allergens, known = list(entry.allergens), entry.composition_known
    reasons: list[str] = []
    amount_g: Decimal | None = None
    conversion_source: str | None = None
    raw_display = ingredient.raw_amount.strip()
    raw_label = clean_amount(ingredient.raw_amount)
    qualitative_label = override.qualitative_label if override is not None else (
        raw_label if raw_label in QUALITATIVE_AMOUNTS else None
    )
    calculation_role = override.nutrition_calculation_role if override is not None else "INCLUDED"

    if override is not None and override.amount is not None and override.unit is not None:
        display_quantity = f"{override.amount}{override.unit}"
        quantity_origin = override.quantity_origin
        amount_g, conversion_source, quantity_warning = resolve_quantity(ingredient, canonical_id, override)
        quantity_kind = IngredientQuantityKind.MEASURED if amount_g is not None else IngredientQuantityKind.UNSPECIFIED
        if quantity_kind == IngredientQuantityKind.UNSPECIFIED:
            display_quantity = raw_display or ("适量" if ingredient.group == "seasoning" else "用量未注明")
            quantity_origin = QuantityOrigin.SOURCE_EXPLICIT if raw_display else QuantityOrigin.DISPLAY_FALLBACK
    elif qualitative_label is not None:
        display_quantity = qualitative_label
        quantity_kind = IngredientQuantityKind.QUALITATIVE
        quantity_origin = override.quantity_origin if override is not None else QuantityOrigin.SOURCE_EXPLICIT
        if calculation_role == "EXCLUDED_MINOR_INGREDIENT" and ingredient.group != "seasoning":
            reasons.append("ONLY_SEASONING_MAY_BE_EXCLUDED_FROM_NUTRITION")
    else:
        amount_g, conversion_source, quantity_warning = resolve_quantity(ingredient, canonical_id, override)
        if amount_g is not None:
            display_quantity = raw_display
            quantity_kind = IngredientQuantityKind.MEASURED
            quantity_origin = QuantityOrigin.SOURCE_EXPLICIT
        else:
            quantity_kind = IngredientQuantityKind.UNSPECIFIED
            quantity_origin = QuantityOrigin.SOURCE_EXPLICIT if raw_display else QuantityOrigin.DISPLAY_FALLBACK
            if raw_display:
                display_quantity = raw_display
            elif ingredient.group == "seasoning":
                display_quantity = "适量"
            else:
                display_quantity = "用量未注明"

    if not known:
        reasons.append("ALLERGEN_COMPOSITION_UNKNOWN")
    return NormalizedDraftIngredient(
        group=ingredient.group,
        raw_name=ingredient.raw_name,
        raw_amount=ingredient.raw_amount,
        canonical_id=canonical_id,
        canonical_name=canonical_name,
        display_quantity=display_quantity,
        quantity_kind=quantity_kind,
        quantity_origin=quantity_origin,
        amount_g=amount_g,
        nutrition_calculation_role=calculation_role,
        allergens=allergens,
        allergen_composition_known=known,
        conversion_source=conversion_source,
        blocking_reasons=list(dict.fromkeys(reasons)),
    )


def normalize_steps(raw: RawMeishiChinaRecipe, curation: RecipeCuration) -> tuple[list[CookingStep], list[str], int, list[int]]:
    reasons: list[str] = []
    values: list[str] = []
    removed_presentation: list[int] = []
    removed_medical = sum(1 for tip in raw.tips if MEDICAL_CLAIM.search(tip))
    for step in raw.cooking_steps:
        if step.step_number in curation.excluded_step_numbers:
            continue
        instruction = curation.step_overrides.get(step.step_number, step.instruction).strip()
        if (
            PRESENTATION_ONLY_STEP.fullmatch(instruction)
            or SOURCE_NOTE_STEP.fullmatch(instruction)
            or _is_ingredient_label_step(raw, instruction)
        ):
            removed_presentation.append(step.step_number)
            continue
        if MEDICAL_CLAIM.search(instruction):
            reasons.append(f"MEDICAL_STEP_TEXT:{step.step_number}")
        if IMAGE_DEPENDENT.search(instruction):
            reasons.append(f"IMAGE_DEPENDENT_STEP:{step.step_number}")
        if not ACTION_WORD.search(instruction):
            reasons.append(f"NON_ACTIONABLE_STEP:{step.step_number}")
        if instruction:
            values.append(instruction)
    steps = [CookingStep(step_number=index, instruction=value) for index, value in enumerate(values, start=1)]
    if not steps:
        reasons.append("COOKING_STEPS_MISSING")
    return steps, list(dict.fromkeys(reasons)), removed_medical, removed_presentation


def resolve_slots(raw: RawMeishiChinaRecipe, curation: RecipeCuration) -> list[MealSlot]:
    if curation.supported_slots:
        return list(dict.fromkeys(curation.supported_slots))
    slots: list[MealSlot] = []
    if "早餐" in raw.categories:
        slots.append(MealSlot.BREAKFAST)
    if any(value in raw.categories for value in {"午餐", "晚餐"}):
        slots.extend([MealSlot.LUNCH, MealSlot.DINNER])
    return list(dict.fromkeys(slots))


def calculate_metrics(
    ingredients: list[NormalizedDraftIngredient],
    servings: Decimal | None,
    foods: list[FoodNutrition],
) -> tuple[Nutrition | None, list[str], list[str], str | None]:
    included = [item for item in ingredients if item.nutrition_calculation_role == "INCLUDED"]
    canonical_ids = {item.canonical_id for item in included if item.canonical_id is not None}
    catalog = {item.canonical_id: item for item in foods}
    missing_nutrition = sorted(canonical_ids - set(catalog))
    unresolved_quantity = sorted(
        item.canonical_id or item.raw_name for item in included if item.amount_g is None
    )
    if servings is None or missing_nutrition or unresolved_quantity or any(item.canonical_id is None for item in included):
        return None, missing_nutrition, unresolved_quantity, None
    totals = {"energy_kcal": Decimal("0"), "protein_g": Decimal("0"), "carbohydrate_g": Decimal("0"), "fat_g": Decimal("0")}
    versions: set[str] = set()
    for ingredient in included:
        assert ingredient.canonical_id is not None and ingredient.amount_g is not None
        food = catalog[ingredient.canonical_id]
        versions.add(food.source.data_version)
        for key in totals:
            totals[key] += getattr(food.nutrition_per_100g, key) * ingredient.amount_g / Decimal("100")
    nutrition = Nutrition(**{key: value / servings for key, value in totals.items()})
    version = versions.pop() if len(versions) == 1 else ("mixed" if versions else None)
    return nutrition, missing_nutrition, unresolved_quantity, version


def _report_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_quality_draft(
    raw: RawMeishiChinaRecipe,
    curation: RecipeCuration,
    foods: list[FoodNutrition],
) -> tuple[StructuredRecipeDraft, RecipeQualityReport]:
    overrides = {normalize_name(item.raw_name): item for item in curation.ingredient_overrides}
    ingredients = [normalize_ingredient(item, overrides.get(normalize_name(item.raw_name))) for item in raw.ingredients]
    steps, step_reasons, removed_medical, removed_presentation = normalize_steps(raw, curation)
    prep_minutes = curation.prep_minutes if curation.prep_minutes is not None else TIME_LABELS.get(raw.source_time_label or "")
    slots = resolve_slots(raw, curation)
    calculated, missing_nutrition, nutrition_unresolved, calculated_version = calculate_metrics(ingredients, curation.servings, foods)
    nutrition = curation.nutrition_per_serving_override or calculated
    nutrition_basis = curation.nutrition_basis or ("CALCULATED_FROM_INGREDIENTS" if calculated is not None else None)
    nutrition_version = curation.nutrition_data_version or calculated_version

    unresolved_names = sorted(item.raw_name for item in ingredients if item.canonical_id is None)
    unresolved_quantities = sorted(item.raw_name for item in ingredients if item.quantity_kind is None)
    unspecified_quantities = sorted(
        item.raw_name for item in ingredients if item.quantity_kind == IngredientQuantityKind.UNSPECIFIED
    )
    unknown_allergens = sorted(item.raw_name for item in ingredients if not item.allergen_composition_known)
    publication_blocking: list[str] = []
    if unresolved_names:
        publication_blocking.append("INGREDIENT_MAPPING_INCOMPLETE")
    if unresolved_quantities:
        publication_blocking.append("INGREDIENT_QUANTITY_INCOMPLETE")
    if curation.servings is None:
        publication_blocking.append("SERVINGS_MISSING")
    if prep_minutes is None:
        publication_blocking.append("TIME_MISSING")
    if not slots:
        publication_blocking.append("MEAL_SLOTS_MISSING")
    publication_blocking.extend(step_reasons)
    for item in ingredients:
        if "ONLY_SEASONING_MAY_BE_EXCLUDED_FROM_NUTRITION" in item.blocking_reasons:
            publication_blocking.append("INVALID_NUTRITION_EXCLUSION")
    publication_blocking = list(dict.fromkeys(publication_blocking))
    solver_blocking = list(publication_blocking)
    if unknown_allergens:
        solver_blocking.append("ALLERGEN_COMPOSITION_INCOMPLETE")
    if curation.nutrition_per_serving_override is None:
        if missing_nutrition:
            solver_blocking.append("NUTRITION_COVERAGE_INCOMPLETE")
        if nutrition_unresolved:
            solver_blocking.append("NUTRITION_QUANTITY_INCOMPLETE")
    if nutrition is None:
        solver_blocking.append("NUTRITION_NOT_CALCULABLE")
    solver_blocking = list(dict.fromkeys(solver_blocking))
    solver_eligible = not solver_blocking
    status = "BLOCKED" if publication_blocking else ("SOLVER_READY" if solver_eligible else "PUBLICATION_READY")
    warnings = list(raw.warnings)
    warnings = [item for item in warnings if item != "EQUIPMENT_NOT_DECLARED"]
    if unspecified_quantities:
        warnings.append("UNSPECIFIED_QUANTITY_PUBLICATION_ONLY")
    if unknown_allergens:
        warnings.append("ALLERGEN_COMPOSITION_INCOMPLETE_PUBLICATION_ONLY")
    if removed_medical:
        warnings.append("MEDICAL_TIPS_EXCLUDED")
    if removed_presentation:
        warnings.append("PRESENTATION_ONLY_STEPS_EXCLUDED")
    draft = StructuredRecipeDraft(
        staging_id=raw.staging_id,
        source_id=raw.source_id,
        source_url=str(raw.source_url),
        source_content_hash=raw.raw_content_hash,
        recipe_id=f"recipe-meishichina-{raw.source_id.rsplit(':', 1)[1]}-v1",
        version=f"mc-{raw.raw_content_hash[:12]}",
        title=curation.title_override or raw.title,
        supported_slots=slots,
        servings=curation.servings,
        prep_minutes=prep_minutes,
        ingredients=ingredients,
        cooking_steps=steps,
        nutrition_per_serving=nutrition,
        nutrition_basis=nutrition_basis,
        solver_eligible=solver_eligible,
        nutrition_data_version=nutrition_version,
        removed_medical_text_count=removed_medical,
        removed_presentation_step_numbers=removed_presentation,
    )
    report_payload: dict[str, object] = {
        "staging_id": raw.staging_id,
        "quality_gate_version": QUALITY_GATE_VERSION,
        "status": status,
        "blocking_reasons": publication_blocking,
        "solver_blocking_reasons": solver_blocking,
        "warnings": list(dict.fromkeys(warnings)),
        "unresolved_ingredient_names": unresolved_names,
        "unresolved_quantity_names": unresolved_quantities,
        "unspecified_quantity_names": unspecified_quantities,
        "unknown_allergen_composition_names": unknown_allergens,
        "missing_nutrition_ids": missing_nutrition,
        "image_independent_steps": not any(reason.startswith("IMAGE_DEPENDENT_STEP") for reason in step_reasons),
        "servings_resolved": curation.servings is not None,
        "time_resolved": prep_minutes is not None,
        "slots_resolved": bool(slots),
        "nutrition_complete": nutrition is not None and not missing_nutrition,
        "solver_eligible": solver_eligible,
    }
    report = RecipeQualityReport(**report_payload, report_hash=_report_hash(report_payload))
    return draft, report
