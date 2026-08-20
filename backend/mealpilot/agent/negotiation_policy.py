"""Bounded, server-verified negotiation policy used by the durable run service."""

from decimal import Decimal

from mealpilot.agent.workflow import run_agent
from mealpilot.domain.models import AgentPlanningCommand, MealPlanningRequest, NegotiationOption, NegotiationProposal, Recipe
from mealpilot.llm.siliconflow import explain_negotiation

def changed_command(command: AgentPlanningCommand, field: str, value: str) -> AgentPlanningCommand:
    if field == "energy_kcal_range":
        low, high = value.split("-", maxsplit=1)
        update = {"energy_kcal_range": {"min": Decimal(low), "max": Decimal(high)}}
    elif field == "max_total_minutes":
        update = {field: int(value)}
    else:
        update = {field: Decimal(value)}
    request_data = command.request.model_dump()
    request_data.update(update)
    command_data = command.model_dump()
    command_data["request"] = MealPlanningRequest.model_validate(request_data).model_dump()
    return AgentPlanningCommand.model_validate(command_data)


def _proposal_candidates(command: AgentPlanningCommand) -> list[tuple[str, str, str]]:
    request = command.request
    return [
        ("max_total_minutes", str(request.max_total_minutes + 15), "增加总烹饪时间；不会改变安全约束。"),
        ("protein_min_g", str((request.protein_min_g * Decimal("0.9")).quantize(Decimal("0.01"))), "降低蛋白质目标；不会改变安全约束。"),
        ("energy_kcal_range", f"{(request.energy_kcal_range.min * Decimal('0.9')).quantize(Decimal('0.1'))}-{(request.energy_kcal_range.max * Decimal('1.1')).quantize(Decimal('0.1'))}", "扩大能量范围；不会改变安全约束。"),
    ]


def build_proposal(command: AgentPlanningCommand, recipes: list[Recipe]) -> NegotiationProposal | None:
    options: list[NegotiationOption] = []
    for field, initial, impact in _proposal_candidates(command):
        value = initial
        for _ in range(12):
            if run_agent(changed_command(command, field, value), recipes).status == "COMPLETED":
                options.append(NegotiationOption(option_id=f"adjust-{field}", field=field, proposed_value=value, impact=impact))
                break
            if field == "max_total_minutes":
                value = str(int(value) + 15)
            elif field == "protein_min_g":
                value = str((Decimal(value) * Decimal("0.9")).quantize(Decimal("0.01")))
            else:
                low, high = value.split("-")
                value = f"{(Decimal(low) * Decimal('0.9')).quantize(Decimal('0.1'))}-{(Decimal(high) * Decimal('1.1')).quantize(Decimal('0.1'))}"
        if options:
            break
    if not options:
        return None
    summary = "; ".join(f"{item.field} -> {item.proposed_value}" for item in options)
    explanation = explain_negotiation(summary) or "当前安全候选集无法满足全部可协商约束。请选择下列一项调整后继续；过敏原和忌口不会被改变。"
    return NegotiationProposal(proposal_id=f"proposal-{command.run_id}", reason_code="CONSTRAINTS_INFEASIBLE", options=options, explanation=explanation)
