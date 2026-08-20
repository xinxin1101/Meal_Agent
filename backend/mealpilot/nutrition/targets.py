"""Versioned, conservative nutrition target suggestions for confirmed healthy adults.

The result is advisory. Planning requests remain explicit user-controlled contracts.
"""

from decimal import Decimal, ROUND_HALF_UP

from mealpilot.domain.models import NutritionTargetSuggestion, UserProfile


_ACTIVITY_FACTORS = {
    "sedentary": Decimal("1.200"),
    "light": Decimal("1.375"),
    "moderate": Decimal("1.550"),
    "active": Decimal("1.725"),
    "very_active": Decimal("1.900"),
}
_GOAL_FACTORS = {"lose": Decimal("0.90"), "maintain": Decimal("1.00"), "gain": Decimal("1.10")}


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def suggest_targets(profile: UserProfile) -> NutritionTargetSuggestion:
    if profile.nutrition_parameter_sex == "unspecified":
        raise ValueError("nutrition calculation parameter must be female or male for an estimate")

    sex_constant = Decimal("5") if profile.nutrition_parameter_sex == "male" else Decimal("-161")
    resting = Decimal("10") * profile.weight_kg + Decimal("6.25") * profile.height_cm - Decimal("5") * Decimal(profile.age_years) + sex_constant
    daily = resting * _ACTIVITY_FACTORS[profile.activity_level.value]
    adjusted = daily * _GOAL_FACTORS[profile.goal.value]
    lower = max(Decimal("0"), _whole(adjusted * Decimal("0.95")))
    upper = _whole(adjusted * Decimal("1.05"))
    protein = (profile.weight_kg * Decimal("0.83")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return NutritionTargetSuggestion(
        energy_kcal_range={"min": lower, "max": upper}, protein_min_g=protein,
        resting_energy_kcal=_whole(resting), estimated_daily_energy_kcal=_whole(daily),
        warnings=[
            "这是面向健康成年人的粗略估算，不构成医疗或个体化营养建议。",
            "活动水平和目标系数可能与实际消耗存在明显偏差；应用前请确认数值。",
            "孕期、未成年人、进食障碍或需要疾病饮食管理时不要使用此估算。",
        ],
        source_references=[
            "Mifflin MD et al. Am J Clin Nutr. 1990;51:241-247. PMID:2305711",
            "WHO/FAO/UNU adult protein reference value: 0.83 g/kg/day",
        ],
    )
