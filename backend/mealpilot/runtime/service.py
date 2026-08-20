from pathlib import Path
from hashlib import sha256
from typing import Literal

from mealpilot.agent.negotiation_policy import build_proposal, changed_command
from mealpilot.agent.workflow import run_agent
from mealpilot.domain.models import AgentPlanningCommand, AgentTraceEvent, DurableRunSnapshot, NegotiationDecision, NegotiationProposal, RunAuditSummary, RunCancelCommand, RunDecisionCommand
from mealpilot.runtime.store import RunRecord, RunStore


class RunConflict(Exception):
    pass


def _quick_verified_proposal(command: AgentPlanningCommand, recipes: list) -> NegotiationProposal | None:
    """The v2 contract uses the fully solved negotiation policy for every option."""
    return None


class DurableRunService:
    def __init__(self, database_path: Path | None = None, *, store: RunStore | None = None) -> None:
        if store is None and database_path is None:
            raise ValueError("database_path or store is required")
        self.store = store or RunStore(database_path)  # type: ignore[arg-type]

    @staticmethod
    def _snapshot(record: RunRecord) -> DurableRunSnapshot:
        return DurableRunSnapshot(
            run_id=record.run_id, status=record.status, run_version=record.run_version,
            result=record.result, proposal=record.proposal,
        )

    def create(self, command: AgentPlanningCommand, idempotency_key: str) -> tuple[DurableRunSnapshot, bool]:
        record, created = self.store.create(command.run_id, command.model_dump(mode="json"), idempotency_key)
        return self._snapshot(record), created

    def get(self, run_id: str) -> DurableRunSnapshot:
        return self._snapshot(self.store.get(run_id))

    def audit_summary(self, run_id: str) -> RunAuditSummary:
        record = self.store.get(run_id)
        profile = record.command.get("profile", {})
        request = record.command.get("request", {})
        profile_snapshot_id = str(profile.get("profile_snapshot_id", "unknown"))
        fingerprint = sha256(profile_snapshot_id.encode("utf-8")).hexdigest()[:16]
        return RunAuditSummary(
            run_id=record.run_id,
            status=record.status,
            run_version=record.run_version,
            profile_fingerprint=fingerprint,
            numeric_policy_version=str(request.get("numeric_policy_version", "unknown")),
            event_types=[event["event_type"] for event in self.store.events_after(run_id, 0)],
        )

    def execute(self, run_id: str, recipes: list, history_recipe_counts: dict[str, int] | None = None, explicit_feedback_scores: dict[str, int] | None = None) -> DurableRunSnapshot:
        record = self.store.get(run_id)
        if record.status == "CANCELLED":
            return self._snapshot(record)
        if record.status != "QUEUED":
            raise RunConflict(run_id)
        running = self.store.transition(run_id, record.run_version, "RUNNING", event_type="started")
        if running is None:
            raise RunConflict(run_id)
        command = AgentPlanningCommand.model_validate(running.command)
        def emit_trace(event: AgentTraceEvent) -> None:
            self.store.append_event(run_id, running.run_version, event.node, {"outcome": event.outcome, "safe_payload": event.safe_payload})

        result = run_agent(command, recipes, emit_trace, history_recipe_counts, explicit_feedback_scores)
        if result.status == "COMPLETED":
            stored = self.store.transition(run_id, running.run_version, "COMPLETED", result=result.model_dump(mode="json"), event_type="completed")
        else:
            proposal = (_quick_verified_proposal(command, recipes) or build_proposal(command, recipes)) if getattr(result.result, "reason_code", None) == "CONSTRAINTS_INFEASIBLE" else None
            status: Literal["PAUSED", "FAILED"] = "PAUSED" if proposal else "FAILED"
            stored = self.store.transition(run_id, running.run_version, status, result=result.model_dump(mode="json"), proposal=proposal.model_dump(mode="json") if proposal else None, event_type="interrupt" if proposal else "failed")
        if stored is None:
            raise RunConflict(run_id)
        return self._snapshot(stored)

    def decide(self, run_id: str, command: RunDecisionCommand, idempotency_key: str, recipes: list, history_recipe_counts: dict[str, int] | None = None, explicit_feedback_scores: dict[str, int] | None = None) -> DurableRunSnapshot:
        saved = self.store.decision_response(run_id, idempotency_key)
        if saved:
            return DurableRunSnapshot.model_validate(saved)
        record = self.store.get(run_id)
        if record.run_version != command.expected_run_version or record.status != "PAUSED":
            raise RunConflict(run_id)
        if record.proposal is None:
            raise RunConflict("legacy negotiation proposal was retired; create a new run")
        proposal = NegotiationProposal.model_validate(record.proposal)
        selected = next((option for option in proposal.options if option.option_id == command.option_id), None)
        if selected is None:
            raise ValueError("unknown negotiation option")
        updated_command = changed_command(AgentPlanningCommand.model_validate(record.command), selected.field, selected.proposed_value)
        running = self.store.transition(run_id, record.run_version, "RUNNING", event_type="resumed")
        if running is None:
            raise RunConflict(run_id)
        def emit_trace(event: AgentTraceEvent) -> None:
            self.store.append_event(run_id, running.run_version, event.node, {"outcome": event.outcome, "safe_payload": event.safe_payload})

        result = run_agent(updated_command, recipes, emit_trace, history_recipe_counts, explicit_feedback_scores)
        stored = self.store.transition(run_id, running.run_version, "COMPLETED" if result.status == "COMPLETED" else "FAILED", result=result.model_dump(mode="json"), event_type="completed" if result.status == "COMPLETED" else "failed")
        if stored is None:
            raise RunConflict(run_id)
        snapshot = self._snapshot(stored)
        self.store.save_decision_response(run_id, idempotency_key, snapshot.model_dump(mode="json"))
        return snapshot

    def cancel(self, run_id: str, command: RunCancelCommand, idempotency_key: str) -> DurableRunSnapshot:
        storage_key = f"cancel:{idempotency_key}"
        saved = self.store.decision_response(run_id, storage_key)
        if saved:
            return DurableRunSnapshot.model_validate(saved)
        record = self.store.get(run_id)
        if record.run_version != command.expected_run_version or record.status not in {"QUEUED", "RUNNING", "PAUSED"}:
            raise RunConflict(run_id)
        stored = self.store.transition(run_id, record.run_version, "CANCELLED", event_type="cancelled")
        if stored is None:
            raise RunConflict(run_id)
        snapshot = self._snapshot(stored)
        self.store.save_decision_response(run_id, storage_key, snapshot.model_dump(mode="json"))
        return snapshot
