from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from mealpilot.domain.models import (
    AdoptedMeal,
    AdoptedMealPlan,
    AgentPlanningCommand,
    AgentRunResult,
    MealPlan,
    PlanningConstraintsSnapshot,
)
from mealpilot.history.store import MealPlanHistoryStore
from mealpilot.runtime.store import RunRecord, RunStore


class AdoptionRejected(Exception):
    """Raised when a run is not a valid, adoptable server result."""


class MealPlanHistoryService:
    def __init__(self, history_store: MealPlanHistoryStore, run_store: RunStore, recipe_titles: dict[str, str], recipe_data_version: str) -> None:
        self.store = history_store
        self.run_store = run_store
        self.recipe_titles = recipe_titles
        self.recipe_data_version = recipe_data_version

    def adopt(self, user_id: str, run_id: str, expected_run_version: int, idempotency_key: str) -> AdoptedMealPlan:
        record = self.run_store.get(run_id)
        if record.run_version != expected_run_version:
            raise AdoptionRejected("stale run version")
        if record.status != "COMPLETED" or record.result is None:
            raise AdoptionRejected("only a completed run can be adopted")
        result = AgentRunResult.model_validate(record.result)
        if not isinstance(result.result, MealPlan):
            raise AdoptionRejected("run did not produce a meal plan")
        plan = result.result
        if plan.validation_report is None or not plan.validation_report.valid or plan.validation_report.totals is None:
            raise AdoptionRejected("plan has no valid deterministic validation report")
        command = AgentPlanningCommand.model_validate(record.command)
        if command.user_id != user_id:
            raise AdoptionRejected("run belongs to a different user")
        snapshot = AdoptedMealPlan(
            history_id=f"history-{uuid4().hex}",
            history_version=1,
            user_id=user_id,
            original_run_id=run_id,
            original_plan_id=plan.plan_id,
            adopted_at=datetime.now(timezone.utc),
            meals=[
                AdoptedMeal(
                    slot=item.slot,
                    recipe_id=item.recipe_id,
                    recipe_version=item.recipe_version,
                    recipe_title=item.recipe_title or self.recipe_titles.get(item.recipe_id, item.recipe_id),
                    portion=item.portion,
                    ingredients=item.ingredients,
                )
                for item in plan.selections
            ],
            verified_totals=plan.validation_report.totals,
            planning_constraints=PlanningConstraintsSnapshot(
                max_total_minutes=command.request.max_total_minutes,
                energy_kcal_range=command.request.energy_kcal_range,
                protein_min_g=command.request.protein_min_g,
            ),
            profile_snapshot_id=command.profile.profile_snapshot_id,
            validation_report=plan.validation_report,
            recipe_data_version=self.recipe_data_version,
            nutrition_data_version=plan.validation_report.nutrition_data_version,
            numeric_policy_version=plan.numeric_policy_version,
        )
        return self.store.adopt(snapshot, idempotency_key)
