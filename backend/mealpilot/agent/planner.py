"""Planning Agent: bounded interpretation and retrieval strategy only."""

from dataclasses import dataclass

from mealpilot.agent.analyze import extract_supported_constraints
from mealpilot.domain.models import AgentPlanningCommand
from mealpilot.llm.siliconflow import extract_planning_constraints, load_siliconflow_settings


@dataclass(frozen=True)
class PlanningBrief:
    retrieval_query: str
    observed_constraints: dict[str, str]
    source: str


def create_planning_brief(command: AgentPlanningCommand) -> PlanningBrief:
    """The structured request remains authoritative; observations never override it."""
    settings = load_siliconflow_settings()
    observations = extract_planning_constraints(command.query) if settings.multi_agent_enabled else None
    if observations is not None:
        return PlanningBrief(command.query, observations, "siliconflow")
    return PlanningBrief(command.query, extract_supported_constraints(command.query), "deterministic_fallback")
