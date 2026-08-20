from decimal import Decimal
from pathlib import Path

import pytest

from mealpilot.data.loader import load_recipes
from mealpilot.domain.models import AgentPlanningCommand, RunCancelCommand, RunDecisionCommand
from mealpilot.runtime.service import DurableRunService, RunConflict


def command(run_id: str) -> AgentPlanningCommand:
    return AgentPlanningCommand.model_validate(
        {
            "run_id": run_id, "query": "时间 1 分钟，蛋白质 90g，1500-1700 kcal",
            "profile": {"profile_snapshot_id": "profile-m4", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
            "request": {"request_id": "request-m4", "max_total_minutes": 1, "energy_kcal_range": {"min": Decimal("1500"), "max": Decimal("1700")}, "protein_min_g": Decimal("90"), "numeric_policy_version": "contract-v2-decimal-v1"},
        }
    )


def test_durable_run_supports_idempotency_occ_and_event_replay(tmp_path: Path) -> None:
    service = DurableRunService(tmp_path / "runs.sqlite3")
    recipes = load_recipes(Path("data/recipes.sample.json"))
    queued, created = service.create(command("run-m4-test"), "create-key")
    duplicate, duplicate_created = service.create(command("different-run"), "create-key")
    assert created and not duplicate_created and duplicate.run_id == queued.run_id

    paused = service.execute(queued.run_id, recipes)
    assert paused.status == "PAUSED" and paused.proposal is not None
    events = service.store.events_after(queued.run_id, 0)
    event_types = [event["event_type"] for event in events]
    assert event_types[:2] == ["queued", "started"] and event_types[-1] == "interrupt"
    assert {"analyze", "retrieve", "solve", "finalize"}.issubset(event_types)

    completed = service.decide(queued.run_id, RunDecisionCommand(option_id=paused.proposal.options[0].option_id, expected_run_version=paused.run_version), "decision-key", recipes)
    replay = service.decide(queued.run_id, RunDecisionCommand(option_id=paused.proposal.options[0].option_id, expected_run_version=paused.run_version), "decision-key", recipes)
    assert completed.status == "COMPLETED" and replay == completed
    resumed_events = service.store.events_after(queued.run_id, 0)
    resumed_types = [event["event_type"] for event in resumed_events]
    interrupt_index = resumed_types.index("interrupt")
    assert "resumed" in resumed_types[interrupt_index + 1:]
    assert {"analyze", "retrieve", "solve", "validate", "finalize", "completed"}.issubset(resumed_types[interrupt_index + 1:])
    with pytest.raises(RunConflict):
        service.decide(queued.run_id, RunDecisionCommand(option_id=paused.proposal.options[0].option_id, expected_run_version=paused.run_version), "new-decision-key", recipes)


def test_completed_run_emits_replayable_agent_timeline(tmp_path: Path) -> None:
    service = DurableRunService(tmp_path / "timeline.sqlite3")
    recipes = load_recipes(Path("data/recipes.sample.json"))
    current = command("run-timeline")
    feasible = current.model_copy(update={"request": current.request.model_copy(update={"max_total_minutes": 60})})
    queued, _ = service.create(feasible, "timeline-create")
    completed = service.execute(queued.run_id, recipes)
    assert completed.status == "COMPLETED"
    event_types = [event["event_type"] for event in service.store.events_after(queued.run_id, 0)]
    assert event_types.index("analyze") < event_types.index("retrieve") < event_types.index("solve") < event_types.index("validate") < event_types.index("finalize")
    assert event_types[-1] == "completed"


def test_queued_run_can_be_cancelled_idempotently_and_never_executes(tmp_path: Path) -> None:
    service = DurableRunService(tmp_path / "cancel.sqlite3")
    recipes = load_recipes(Path("data/recipes.sample.json"))
    queued, _ = service.create(command("run-cancel"), "cancel-create")
    cancelled = service.cancel(queued.run_id, RunCancelCommand(expected_run_version=0), "cancel-key")
    replay = service.cancel(queued.run_id, RunCancelCommand(expected_run_version=0), "cancel-key")
    assert cancelled.status == "CANCELLED" and replay == cancelled
    assert service.execute(queued.run_id, recipes).status == "CANCELLED"
    assert [event["event_type"] for event in service.store.events_after(queued.run_id, 0)] == ["queued", "cancelled"]
