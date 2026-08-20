from mealpilot.agent.explainer import explain_plan
from mealpilot.agent.planner import create_planning_brief
from mealpilot.agent.retrieval import retrieve_recipes
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict
from mealpilot.domain.models import (
    AgentPlanningCommand,
    AgentRunResult,
    AgentRunStatus,
    AgentTraceEvent,
    PlanningFailure,
    Recipe,
)
from mealpilot.planning.candidates import filter_safe_candidates
from mealpilot.planning.engine import create_deterministic_plan
from collections.abc import Callable


class _AgentGraphState(TypedDict):
    command: AgentPlanningCommand
    recipes: list[dict]
    result: AgentRunResult
    event_sink: Callable[[AgentTraceEvent], None] | None


def _run_agent_loop(command: AgentPlanningCommand, recipes: list[Recipe], event_sink: Callable[[AgentTraceEvent], None] | None = None, history_recipe_counts: dict[str, int] | None = None, explicit_feedback_scores: dict[str, int] | None = None) -> AgentRunResult:
    """Bounded M2 loop. Only retrieval breadth may be retried; constraints never relax."""
    brief = create_planning_brief(command)
    parsed = brief.observed_constraints
    trace: list[AgentTraceEvent] = []

    def record(event: AgentTraceEvent) -> None:
        trace.append(event)
        if event_sink is not None:
            event_sink(event)

    def plan(candidate_recipes: list[Recipe]) -> object:
        if history_recipe_counts:
            return create_deterministic_plan(candidate_recipes, command.profile, command.request, history_recipe_counts, explicit_feedback_scores)
        return create_deterministic_plan(candidate_recipes, command.profile, command.request)

    record(AgentTraceEvent(node="analyze", outcome="PLANNING_BRIEF_CREATED", safe_payload={"fields": sorted(parsed), "source": brief.source}))
    strategies = ["PLANNING_AGENT", "INITIAL_RETRIEVAL"]
    safe_recipes, coverage = filter_safe_candidates(recipes, command.profile)
    if coverage.status != "READY":
        result = plan(recipes)
        record(AgentTraceEvent(node="retrieve", outcome=coverage.status, safe_payload={"excluded_recipe_ids": coverage.excluded_recipe_ids}))
        record(AgentTraceEvent(node="finalize", outcome="FAILED", safe_payload={"reason_code": result.reason_code}))
        return AgentRunResult(run_id=command.run_id, status=AgentRunStatus.FAILED, parsed_constraints=parsed, attempted_strategies=strategies, trace=trace, result=result)

    retrieved = retrieve_recipes(brief.retrieval_query, safe_recipes, command.retrieval_top_k_per_slot, history_recipe_counts, explicit_feedback_scores)
    record(AgentTraceEvent(node="retrieve", outcome="BM25_RETRIEVED", safe_payload={"recipe_ids": [recipe.recipe_id for recipe in retrieved], "history_adjusted": int(bool(history_recipe_counts))}))
    result = plan(retrieved)
    record(AgentTraceEvent(node="solve", outcome=getattr(result, "solver_status", getattr(result, "status", "FAILED")), safe_payload={}))
    if isinstance(result, PlanningFailure) and len(retrieved) < len(safe_recipes):
        strategies.append("EXPAND_RETRIEVAL")
        record(AgentTraceEvent(node="repair", outcome="RETRY_WITH_SAFE_CORPUS", safe_payload={"recipe_count": len(safe_recipes)}))
        result = plan(safe_recipes)
        record(AgentTraceEvent(node="solve", outcome=getattr(result, "solver_status", getattr(result, "status", "FAILED")), safe_payload={}))
    if isinstance(result, PlanningFailure):
        record(AgentTraceEvent(node="finalize", outcome="FAILED", safe_payload={"reason_code": result.reason_code}))
        return AgentRunResult(run_id=command.run_id, status=AgentRunStatus.FAILED, parsed_constraints=parsed, attempted_strategies=strategies, trace=trace, result=result)
    record(AgentTraceEvent(node="validate", outcome="VALID", safe_payload={}))
    explanation = explain_plan(result, {recipe.recipe_id: recipe.title for recipe in safe_recipes})
    record(AgentTraceEvent(node="finalize", outcome="COMPLETED", safe_payload={"explanation_source": "llm" if explanation else "deterministic"}))
    return AgentRunResult(run_id=command.run_id, status=AgentRunStatus.COMPLETED, parsed_constraints=parsed, attempted_strategies=strategies, trace=trace, result=result, explanation=explanation)


def _agent_loop_node(state: _AgentGraphState) -> dict[str, AgentRunResult]:
    recipes = [Recipe.model_validate(item) for item in state["recipes"]]
    return {"result": _run_agent_loop(state["command"], recipes, state["event_sink"])}


def _build_agent_graph() -> object:
    graph = StateGraph(_AgentGraphState)
    graph.add_node("bounded_orchestrator", _agent_loop_node)
    graph.add_edge(START, "bounded_orchestrator")
    graph.add_edge("bounded_orchestrator", END)
    return graph.compile()


_AGENT_GRAPH = _build_agent_graph()


def run_agent(command: AgentPlanningCommand, recipes: list[Recipe], event_sink: Callable[[AgentTraceEvent], None] | None = None, history_recipe_counts: dict[str, int] | None = None, explicit_feedback_scores: dict[str, int] | None = None) -> AgentRunResult:
    """Execute the M2 bounded orchestrator through a LangGraph state transition."""
    if history_recipe_counts or explicit_feedback_scores:
        return _run_agent_loop(command, recipes, event_sink, history_recipe_counts, explicit_feedback_scores)
    return _AGENT_GRAPH.invoke({"command": command, "recipes": [recipe.model_dump(mode="json") for recipe in recipes], "event_sink": event_sink})["result"]
