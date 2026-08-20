from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from ortools.sat.python import cp_model

from mealpilot.domain.models import MealPlan, MealPlanningRequest, MealSlot, PlanSelection, PlanTotals, Recipe, SolverStatus

PORTION_STEPS = (1, 2, 3, 4)  # Half-serving units.
INT64_MAX = 9_223_372_036_854_775_807


class NumericRangeError(ValueError):
    """Raised before CP-SAT receives a coefficient outside its signed int64 contract."""


def _floor_scaled(value: Decimal, scale: int) -> int:
    return _checked_int64((value * scale).to_integral_value(rounding=ROUND_FLOOR))


def _ceil_scaled(value: Decimal, scale: int) -> int:
    return _checked_int64((value * scale).to_integral_value(rounding=ROUND_CEILING))


def _checked_int64(value: Decimal) -> int:
    scaled = int(value)
    if abs(scaled) > INT64_MAX:
        raise NumericRangeError("scaled solver coefficient exceeds signed int64")
    return scaled


def solve_day(recipes: list[Recipe], request: MealPlanningRequest, history_recipe_counts: dict[str, int] | None = None, explicit_feedback_scores: dict[str, int] | None = None) -> tuple[MealPlan | None, SolverStatus]:
    """Solve safely scaled CP-SAT model; post-solve Decimal validation remains mandatory."""
    model = cp_model.CpModel()
    ordered = sorted(recipes, key=lambda item: item.recipe_id)
    decision: dict[tuple[str, MealSlot, int], cp_model.IntVar] = {}
    for recipe in ordered:
        for slot in recipe.supported_slots:
            for step in PORTION_STEPS:
                decision[recipe.recipe_id, slot, step] = model.NewBoolVar(f"pick_{recipe.recipe_id}_{slot}_{step}")

    for slot in request.meal_slots:
        variables = [var for (recipe_id, candidate_slot, _), var in decision.items() if candidate_slot == slot]
        if not variables:
            return None, SolverStatus.INFEASIBLE
        model.Add(sum(variables) == 1)

    def weighted(metric: str, scale: int, rounding: str) -> cp_model.LinearExpr:
        pieces = []
        magnitudes = []
        for (recipe_id, _slot, step), variable in decision.items():
            recipe = next(item for item in ordered if item.recipe_id == recipe_id)
            if recipe.nutrition_per_serving is None:
                raise ValueError("solver received a recipe without authoritative nutrition")
            value = getattr(recipe.nutrition_per_serving, metric)
            contribution = value * Decimal(step) / Decimal(2)
            scaled = _floor_scaled(contribution, scale) if rounding == "floor" else _ceil_scaled(contribution, scale)
            pieces.append(scaled * variable)
            magnitudes.append(abs(scaled))
        if sum(magnitudes) > INT64_MAX:
            raise NumericRangeError("scaled solver expression exceeds signed int64")
        return sum(pieces)

    energy_min = weighted("energy_kcal", 10, "floor")
    energy_max = weighted("energy_kcal", 10, "ceil")
    protein_min = weighted("protein_g", 100, "floor")
    model.Add(energy_min >= _ceil_scaled(request.energy_kcal_range.min, 10))
    model.Add(energy_max <= _floor_scaled(request.energy_kcal_range.max, 10))
    model.Add(protein_min >= _ceil_scaled(request.protein_min_g, 100))
    model.Add(sum(recipe.prep_minutes * var for (recipe_id, _slot, _step), var in decision.items() for recipe in ordered if recipe.recipe_id == recipe_id) <= request.max_total_minutes)

    midpoint = (request.energy_kcal_range.min + request.energy_kcal_range.max) / Decimal(2)
    energy_deviation = model.NewIntVar(0, 1_000_000, "energy_deviation")
    energy_target = _floor_scaled(midpoint, 10)
    model.AddAbsEquality(energy_deviation, energy_max - energy_target)
    repetition = model.NewIntVar(0, 3, "repetition")
    repetition_parts = []
    for recipe in ordered:
        count = sum(var for (recipe_id, _slot, _step), var in decision.items() if recipe_id == recipe.recipe_id)
        repeated = model.NewIntVar(0, 2, f"repeat_{recipe.recipe_id}")
        model.Add(repeated >= count - 1)
        repetition_parts.append(repeated)
    model.Add(repetition == sum(repetition_parts))
    history_recipe_counts = history_recipe_counts or {}
    history_repetition = sum(
        min(history_recipe_counts.get(recipe_id, 0), 10) * variable
        for (recipe_id, _slot, _step), variable in decision.items()
    )
    explicit_feedback_scores = explicit_feedback_scores or {}
    feedback_cost = sum(
        explicit_feedback_scores.get(recipe_id, 0) * variable
        for (recipe_id, _slot, _step), variable in decision.items()
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 20260806

    def run(objective: cp_model.LinearExpr) -> int:
        model.Minimize(objective)
        return solver.Solve(model)

    first = run(energy_deviation)
    if first == cp_model.INFEASIBLE:
        return None, SolverStatus.INFEASIBLE
    if first not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, SolverStatus.UNKNOWN
    model.Add(energy_deviation == solver.Value(energy_deviation))
    second = run(repetition)
    if second not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, SolverStatus.UNKNOWN
    model.Add(repetition == solver.Value(repetition))
    third = run(feedback_cost)
    if third not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, SolverStatus.UNKNOWN
    model.Add(feedback_cost == solver.Value(feedback_cost))
    fourth = run(history_repetition)
    if fourth not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, SolverStatus.UNKNOWN

    selections = []
    for (recipe_id, slot, step), variable in sorted(decision.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
        if solver.Value(variable):
            recipe = next(item for item in ordered if item.recipe_id == recipe_id)
            selections.append(PlanSelection(
                slot=slot,
                recipe_id=recipe.recipe_id,
                recipe_version=recipe.version,
                portion=f"{Decimal(step) / Decimal(2):.1f}",
                recipe_title=recipe.title,
                ingredients=recipe.ingredients,
            ))
    placeholder = PlanTotals(energy_kcal=Decimal("0"), protein_g=Decimal("0"), carbohydrate_g=Decimal("0"), fat_g=Decimal("0"), prep_minutes=0)
    final_status = SolverStatus.OPTIMAL if fourth == cp_model.OPTIMAL else SolverStatus.FEASIBLE
    return MealPlan(plan_id=f"plan-{request.request_id}", selections=selections, totals=placeholder, solver_status=final_status, validation_report=None, numeric_policy_version=request.numeric_policy_version), final_status
