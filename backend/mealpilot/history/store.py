from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mealpilot.domain.contract_migration import migrate_persisted_document
from mealpilot.domain.models import AdoptedMealPlan, FeedbackCollection, HistoryCollection, PlanFeedback, SavePlanFeedbackCommand


def _adopted_from_json(raw: str) -> AdoptedMealPlan:
    return AdoptedMealPlan.model_validate(migrate_persisted_document(json.loads(raw)))


class HistoryConflict(Exception):
    """Raised for stale collection versions or mismatched idempotency requests."""


class MealPlanHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS history_meta (
                    user_id TEXT PRIMARY KEY,
                    collection_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS adopted_plans (
                    history_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    original_run_id TEXT NOT NULL,
                    original_plan_id TEXT NOT NULL,
                    adopted_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(user_id, original_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_adopted_plans_user_time
                ON adopted_plans(user_id, adopted_at DESC, history_id DESC);
                CREATE TABLE IF NOT EXISTS history_idempotency (
                    user_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY(user_id, operation, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS deleted_history_runs (
                    user_id TEXT NOT NULL,
                    original_run_id TEXT NOT NULL,
                    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, original_run_id)
                );
                CREATE TABLE IF NOT EXISTS plan_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    history_id TEXT NOT NULL UNIQUE,
                    feedback_version INTEGER NOT NULL,
                    submitted_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_plan_feedback_user_time
                ON plan_feedback(user_id, submitted_at DESC);
                CREATE TABLE IF NOT EXISTS feedback_idempotency (
                    user_id TEXT NOT NULL,
                    history_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY(user_id, history_id, idempotency_key)
                );
                """
            )

    def collection(self, user_id: str, limit: int = 50) -> HistoryCollection:
        with self._connection() as connection:
            version = self._version(connection, user_id)
            rows = connection.execute(
                "SELECT snapshot_json FROM adopted_plans WHERE user_id=? ORDER BY adopted_at DESC, history_id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return HistoryCollection(
            user_id=user_id,
            collection_version=version,
            items=[_adopted_from_json(row[0]) for row in rows],
        )

    def recent(self, user_id: str, limit: int = 10) -> list[AdoptedMealPlan]:
        return self.collection(user_id, limit=limit).items

    def get(self, user_id: str, history_id: str) -> AdoptedMealPlan:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM adopted_plans WHERE user_id=? AND history_id=?",
                (user_id, history_id),
            ).fetchone()
        if row is None:
            raise KeyError(history_id)
        return _adopted_from_json(row[0])

    def adopt(self, plan: AdoptedMealPlan, idempotency_key: str) -> AdoptedMealPlan:
        operation = "adopt"
        with self._lock, self._connection() as connection:
            tombstone = connection.execute(
                "SELECT 1 FROM deleted_history_runs WHERE user_id=? AND original_run_id=?",
                (plan.user_id, plan.original_run_id),
            ).fetchone()
            if tombstone is not None:
                raise HistoryConflict("a deleted adoption cannot be replayed; create a new plan")
            replay = self._replay(connection, plan.user_id, operation, idempotency_key)
            if replay is not None:
                return AdoptedMealPlan.model_validate(replay)
            existing = connection.execute(
                "SELECT snapshot_json FROM adopted_plans WHERE user_id=? AND original_run_id=?",
                (plan.user_id, plan.original_run_id),
            ).fetchone()
            if existing is not None:
                adopted = _adopted_from_json(existing[0])
            else:
                connection.execute(
                    "INSERT INTO adopted_plans(history_id,user_id,original_run_id,original_plan_id,adopted_at,snapshot_json) VALUES(?,?,?,?,?,?)",
                    (plan.history_id, plan.user_id, plan.original_run_id, plan.original_plan_id, plan.adopted_at.isoformat(), plan.model_dump_json()),
                )
                self._bump(connection, plan.user_id)
                adopted = plan
            self._save_replay(connection, plan.user_id, operation, idempotency_key, adopted.model_dump(mode="json"))
            return adopted

    def feedback_collection(self, user_id: str) -> FeedbackCollection:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM plan_feedback WHERE user_id=? ORDER BY submitted_at DESC, feedback_id DESC",
                (user_id,),
            ).fetchall()
        return FeedbackCollection(user_id=user_id, items=[PlanFeedback.model_validate_json(row[0]) for row in rows])

    def get_feedback(self, user_id: str, history_id: str) -> PlanFeedback | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM plan_feedback WHERE user_id=? AND history_id=?",
                (user_id, history_id),
            ).fetchone()
        return PlanFeedback.model_validate_json(row[0]) if row else None

    def save_feedback(self, user_id: str, history_id: str, command: SavePlanFeedbackCommand, idempotency_key: str) -> PlanFeedback:
        request_json = command.model_dump_json()
        with self._lock, self._connection() as connection:
            if connection.execute("SELECT 1 FROM adopted_plans WHERE user_id=? AND history_id=?", (user_id, history_id)).fetchone() is None:
                raise KeyError(history_id)
            replay = connection.execute(
                "SELECT request_json,response_json FROM feedback_idempotency WHERE user_id=? AND history_id=? AND idempotency_key=?",
                (user_id, history_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if replay[0] != request_json:
                    raise HistoryConflict("feedback idempotency key was reused for a different request")
                return PlanFeedback.model_validate_json(replay[1])
            existing = connection.execute(
                "SELECT feedback_id,feedback_version FROM plan_feedback WHERE user_id=? AND history_id=?",
                (user_id, history_id),
            ).fetchone()
            current_version = int(existing[1]) if existing else 0
            if current_version != command.expected_feedback_version:
                raise HistoryConflict("stale feedback version")
            feedback = PlanFeedback(
                feedback_id=existing[0] if existing else f"feedback-{uuid4().hex}",
                user_id=user_id,
                history_id=history_id,
                feedback_version=current_version + 1,
                submitted_at=datetime.now(timezone.utc),
                meals=command.meals,
                directives=command.directives,
            )
            connection.execute(
                """INSERT INTO plan_feedback(feedback_id,user_id,history_id,feedback_version,submitted_at,snapshot_json)
                VALUES(?,?,?,?,?,?) ON CONFLICT(history_id) DO UPDATE SET feedback_version=excluded.feedback_version,
                submitted_at=excluded.submitted_at,snapshot_json=excluded.snapshot_json""",
                (feedback.feedback_id, user_id, history_id, feedback.feedback_version, feedback.submitted_at.isoformat(), feedback.model_dump_json()),
            )
            connection.execute(
                "INSERT INTO feedback_idempotency(user_id,history_id,idempotency_key,request_json,response_json) VALUES(?,?,?,?,?)",
                (user_id, history_id, idempotency_key, request_json, feedback.model_dump_json()),
            )
            return feedback

    def explicit_recipe_scores(self, user_id: str) -> dict[str, int]:
        """Positive means avoid; negative means reuse. Only explicit submitted feedback contributes."""
        scores: dict[str, int] = {}
        plans = {item.history_id: item for item in self.collection(user_id).items}
        for feedback in self.feedback_collection(user_id).items:
            plan = plans.get(feedback.history_id)
            if plan is None:
                continue
            by_slot = {meal.slot: meal.recipe_id for meal in plan.meals}
            for meal in feedback.meals:
                recipe_id = by_slot.get(meal.slot)
                if recipe_id is None:
                    continue
                outcome_score = {"COMPLETED": -1, "SKIPPED": 2, "REPLACED": 3}[meal.outcome]
                scores[recipe_id] = scores.get(recipe_id, 0) + outcome_score
                if meal.outcome == "REPLACED" and meal.replacement_recipe_id:
                    scores[meal.replacement_recipe_id] = scores.get(meal.replacement_recipe_id, 0) - 2
                if meal.rating is not None:
                    scores[recipe_id] = scores.get(recipe_id, 0) + (3 - meal.rating)
            for directive in feedback.directives:
                scores[directive.recipe_id] = scores.get(directive.recipe_id, 0) + (6 if directive.action == "AVOID" else -6)
        return {recipe_id: max(-10, min(10, score)) for recipe_id, score in scores.items() if score}

    def delete(self, user_id: str, history_id: str, expected_version: int, idempotency_key: str) -> HistoryCollection:
        operation = f"delete:{history_id}"
        with self._lock, self._connection() as connection:
            replay = self._replay(connection, user_id, operation, idempotency_key)
            if replay is not None:
                return HistoryCollection.model_validate(replay)
            if self._version(connection, user_id) != expected_version:
                raise HistoryConflict("stale history collection version")
            row = connection.execute("SELECT original_run_id FROM adopted_plans WHERE user_id=? AND history_id=?", (user_id, history_id)).fetchone()
            if row is None:
                raise KeyError(history_id)
            connection.execute("INSERT OR IGNORE INTO deleted_history_runs(user_id,original_run_id) VALUES(?,?)", (user_id, row[0]))
            connection.execute("DELETE FROM adopted_plans WHERE user_id=? AND history_id=?", (user_id, history_id))
            connection.execute("DELETE FROM plan_feedback WHERE user_id=? AND history_id=?", (user_id, history_id))
            connection.execute("DELETE FROM feedback_idempotency WHERE user_id=? AND history_id=?", (user_id, history_id))
            connection.execute("DELETE FROM history_idempotency WHERE user_id=?", (user_id,))
            self._bump(connection, user_id)
            result = self._collection_in_connection(connection, user_id)
            self._save_replay(connection, user_id, operation, idempotency_key, result.model_dump(mode="json"))
            return result

    def clear(self, user_id: str, expected_version: int, idempotency_key: str) -> HistoryCollection:
        operation = "clear"
        with self._lock, self._connection() as connection:
            replay = self._replay(connection, user_id, operation, idempotency_key)
            if replay is not None:
                return HistoryCollection.model_validate(replay)
            if self._version(connection, user_id) != expected_version:
                raise HistoryConflict("stale history collection version")
            connection.execute("INSERT OR IGNORE INTO deleted_history_runs(user_id,original_run_id) SELECT user_id,original_run_id FROM adopted_plans WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM adopted_plans WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM plan_feedback WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM feedback_idempotency WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM history_idempotency WHERE user_id=?", (user_id,))
            self._bump(connection, user_id)
            result = self._collection_in_connection(connection, user_id)
            self._save_replay(connection, user_id, operation, idempotency_key, result.model_dump(mode="json"))
            return result

    def delete_all_for_account(self, user_id: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM plan_feedback WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM feedback_idempotency WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM adopted_plans WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM deleted_history_runs WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM history_idempotency WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM history_meta WHERE user_id=?", (user_id,))

    def _collection_in_connection(self, connection: sqlite3.Connection, user_id: str) -> HistoryCollection:
        rows = connection.execute(
            "SELECT snapshot_json FROM adopted_plans WHERE user_id=? ORDER BY adopted_at DESC, history_id DESC LIMIT 50",
            (user_id,),
        ).fetchall()
        return HistoryCollection(user_id=user_id, collection_version=self._version(connection, user_id), items=[_adopted_from_json(row[0]) for row in rows])

    @staticmethod
    def _version(connection: sqlite3.Connection, user_id: str) -> int:
        row = connection.execute("SELECT collection_version FROM history_meta WHERE user_id=?", (user_id,)).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _bump(connection: sqlite3.Connection, user_id: str) -> None:
        connection.execute(
            "INSERT INTO history_meta(user_id,collection_version) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET collection_version=collection_version+1",
            (user_id,),
        )

    @staticmethod
    def _replay(connection: sqlite3.Connection, user_id: str, operation: str, key: str) -> dict | None:
        row = connection.execute(
            "SELECT response_json FROM history_idempotency WHERE user_id=? AND operation=? AND idempotency_key=?",
            (user_id, operation, key),
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _save_replay(connection: sqlite3.Connection, user_id: str, operation: str, key: str, response: dict) -> None:
        connection.execute(
            "INSERT INTO history_idempotency(user_id,operation,idempotency_key,response_json) VALUES(?,?,?,?)",
            (user_id, operation, key, json.dumps(response, ensure_ascii=False)),
        )

    def _connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
