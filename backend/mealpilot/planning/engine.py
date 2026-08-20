from mealpilot.domain.models import (
    CoverageStatus,
    MealPlan,
    MealPlanningRequest,
    PlanningFailure,
    Recipe,
    SolverStatus,
    UserProfile,
)
from mealpilot.planning.candidates import filter_safe_candidates
from mealpilot.planning.solver import NumericRangeError, solve_day
from mealpilot.planning.validation import validate_plan


def create_deterministic_plan(
    recipes: list[Recipe], profile: UserProfile, request: MealPlanningRequest, history_recipe_counts: dict[str, int] | None = None,
    explicit_feedback_scores: dict[str, int] | None = None,
) -> MealPlan | PlanningFailure:
    candidates, coverage = filter_safe_candidates(recipes, profile)
    if coverage.status != CoverageStatus.READY:
        reason = "NO_SAFE_CANDIDATES" if coverage.status == CoverageStatus.EMPTY_RETRIEVAL else "INSUFFICIENT_MEAL_SLOT_COVERAGE"
        return PlanningFailure(status=coverage.status, reason_code=reason, message="Local recipe data cannot safely cover all required meal slots.", coverage_report=coverage)

    try:
        plan, status = solve_day(candidates, request, history_recipe_counts, explicit_feedback_scores)
    except NumericRangeError:
        return PlanningFailure(status=SolverStatus.UNKNOWN, reason_code="NUMERIC_RANGE_EXCEEDED", message="The request or recipe data exceeds the deterministic solver numeric range.", coverage_report=coverage)
    if plan is None:
        reason = "CONSTRAINTS_INFEASIBLE" if status == SolverStatus.INFEASIBLE else "SOLVER_UNKNOWN"
        return PlanningFailure(status=status, reason_code=reason, message="No validated plan was produced within the deterministic solver contract.", coverage_report=coverage)

    report = validate_plan(plan, {recipe.recipe_id: recipe for recipe in candidates}, profile, request)
    if not report.valid or report.totals is None:
        return PlanningFailure(status=SolverStatus.INFEASIBLE, reason_code="POST_SOLVE_VALIDATION_FAILED", message="Solver output failed authoritative Decimal validation.", coverage_report=coverage)
    return plan.model_copy(update={"totals": report.totals, "validation_report": report})
