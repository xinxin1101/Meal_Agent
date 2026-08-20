from decimal import Decimal
from pathlib import Path

from mealpilot.agent.analyze import extract_supported_constraints
from mealpilot.agent import workflow
from mealpilot.agent.workflow import run_agent
from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import AgentPlanningCommand, PlanningFailure, SolverStatus
from mealpilot.planning.candidates import filter_safe_candidates


def command() -> AgentPlanningCommand:
    return AgentPlanningCommand.model_validate(
        {
            "run_id": "run-m2-test", "query": "牛肉餐；时间 60 分钟；蛋白质 90g；1500-1700 kcal",
            "profile": {"profile_snapshot_id": "profile-m2", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
            "request": {"request_id": "request-m2", "max_total_minutes": 60, "energy_kcal_range": {"min": Decimal("1500"), "max": Decimal("1700")}, "protein_min_g": Decimal("90"), "numeric_policy_version": "contract-v2-decimal-v1"},
        }
    )


def test_bounded_extractor_reads_only_supported_numeric_constraints() -> None:
    extracted = extract_supported_constraints(command().query)
    assert extracted == {"max_total_minutes": "60", "protein_min_g": "90", "energy_kcal_range": "1500-1700"}


def test_agent_orchestrates_retrieval_solver_validation_and_trace() -> None:
    recipes = load_recipes(Path("data/recipes.sample.json"))
    result = run_agent(command(), recipes)
    assert result.status == "COMPLETED"
    assert result.result.validation_report is not None and result.result.validation_report.valid
    assert [event.node for event in result.trace] == ["analyze", "retrieve", "solve", "validate", "finalize"]
    assert result.parsed_constraints["protein_min_g"] == "90"


def test_agent_retries_only_by_expanding_safe_retrieval(monkeypatch: object) -> None:
    recipes = load_recipes(Path("data/recipes.sample.json"))
    original = workflow.create_deterministic_plan

    def fail_narrow_retrieval(current_recipes: object, current_profile: object, current_request: object) -> object:
        if len(current_recipes) < len(recipes):
            _, coverage = filter_safe_candidates(recipes, current_profile)
            return PlanningFailure(status=SolverStatus.INFEASIBLE, reason_code="CONSTRAINTS_INFEASIBLE", message="test-only narrow retrieval", coverage_report=coverage)
        return original(current_recipes, current_profile, current_request)

    monkeypatch.setattr(workflow, "create_deterministic_plan", fail_narrow_retrieval)
    result = workflow.run_agent(command(), recipes)
    assert result.status == "COMPLETED"
    assert result.attempted_strategies == ["PLANNING_AGENT", "INITIAL_RETRIEVAL", "EXPAND_RETRIEVAL"]
    assert any(event.node == "repair" for event in result.trace)
