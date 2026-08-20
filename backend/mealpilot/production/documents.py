from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from mealpilot.conversation.store import ConversationConflict
from mealpilot.domain.contract_migration import migrate_persisted_document
from mealpilot.domain.models import (
    AdoptedMealPlan, ChatResponse, ConversationDetail, ConversationMessage,
    ConversationSummary, FeedbackCollection, HistoryCollection, PlanFeedback,
    SavePlanFeedbackCommand,
)
from mealpilot.history.store import HistoryConflict
from mealpilot.production.postgres import _psycopg


class _PostgresBase:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        psycopg, dict_row = _psycopg()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection


class PostgresMealPlanHistoryStore(_PostgresBase):
    def delete_all_for_account(self, user_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM operation_idempotency WHERE user_id=%s", (user_id,))
            connection.execute("DELETE FROM history_documents WHERE user_id=%s", (user_id,))

    """User-scoped immutable plan snapshots stored transactionally in PostgreSQL."""

    def collection(self, user_id: str, limit: int = 50) -> HistoryCollection:
        with self._connection() as connection:
            row = connection.execute("SELECT collection_version,items_json FROM history_documents WHERE user_id=%s", (user_id,)).fetchone()
        items = [AdoptedMealPlan.model_validate(migrate_persisted_document(item)) for item in (row["items_json"] if row else [])]
        return HistoryCollection(user_id=user_id, collection_version=int(row["collection_version"]) if row else 0, items=items[:limit])

    def recent(self, user_id: str, limit: int = 10) -> list[AdoptedMealPlan]:
        return self.collection(user_id, limit).items

    def get(self, user_id: str, history_id: str) -> AdoptedMealPlan:
        item = next((item for item in self.collection(user_id).items if item.history_id == history_id), None)
        if item is None:
            raise KeyError(history_id)
        return item

    def adopt(self, plan: AdoptedMealPlan, idempotency_key: str) -> AdoptedMealPlan:
        fingerprint = self._fingerprint(plan.model_dump(mode="json"))
        with self._connection() as connection:
            replay = self._replay(connection, plan.user_id, "adopt", idempotency_key, fingerprint, AdoptedMealPlan)
            if replay:
                return replay
            row = self._locked_document(connection, plan.user_id)
            if plan.original_run_id in row["deleted_run_ids"]:
                raise HistoryConflict("a deleted adoption cannot be replayed; create a new plan")
            items = [AdoptedMealPlan.model_validate(migrate_persisted_document(item)) for item in row["items_json"]]
            adopted = next((item for item in items if item.original_run_id == plan.original_run_id), None)
            if adopted is None:
                items.insert(0, plan)
                self._save_document(connection, plan.user_id, int(row["collection_version"]) + 1, items, row["feedback_json"], row["deleted_run_ids"])
                adopted = plan
            self._save_replay(connection, plan.user_id, "adopt", idempotency_key, fingerprint, adopted.model_dump(mode="json"))
            return adopted

    def feedback_collection(self, user_id: str) -> FeedbackCollection:
        with self._connection() as connection:
            row = connection.execute("SELECT feedback_json FROM history_documents WHERE user_id=%s", (user_id,)).fetchone()
        return FeedbackCollection(user_id=user_id, items=[PlanFeedback.model_validate(item) for item in (row["feedback_json"] if row else [])])

    def get_feedback(self, user_id: str, history_id: str) -> PlanFeedback | None:
        return next((item for item in self.feedback_collection(user_id).items if item.history_id == history_id), None)

    def save_feedback(self, user_id: str, history_id: str, command: SavePlanFeedbackCommand, idempotency_key: str) -> PlanFeedback:
        fingerprint = self._fingerprint(command.model_dump(mode="json"))
        operation = f"feedback:{history_id}"
        with self._connection() as connection:
            replay = self._replay(connection, user_id, operation, idempotency_key, fingerprint, PlanFeedback)
            if replay:
                return replay
            row = self._locked_document(connection, user_id)
            items = [AdoptedMealPlan.model_validate(migrate_persisted_document(item)) for item in row["items_json"]]
            if not any(item.history_id == history_id for item in items):
                raise KeyError(history_id)
            feedback_items = [PlanFeedback.model_validate(item) for item in row["feedback_json"]]
            existing = next((item for item in feedback_items if item.history_id == history_id), None)
            current_version = existing.feedback_version if existing else 0
            if current_version != command.expected_feedback_version:
                raise HistoryConflict("stale feedback version")
            feedback = PlanFeedback(
                feedback_id=existing.feedback_id if existing else f"feedback-{uuid4().hex}",
                user_id=user_id, history_id=history_id, feedback_version=current_version + 1,
                submitted_at=datetime.now(timezone.utc), meals=command.meals, directives=command.directives,
            )
            feedback_items = [item for item in feedback_items if item.history_id != history_id]
            feedback_items.insert(0, feedback)
            self._save_document(connection, user_id, int(row["collection_version"]), items, feedback_items, row["deleted_run_ids"])
            self._save_replay(connection, user_id, operation, idempotency_key, fingerprint, feedback.model_dump(mode="json"))
            return feedback

    def explicit_recipe_scores(self, user_id: str) -> dict[str, int]:
        scores: dict[str, int] = {}
        plans = {item.history_id: item for item in self.collection(user_id).items}
        for feedback in self.feedback_collection(user_id).items:
            plan = plans.get(feedback.history_id)
            if not plan:
                continue
            by_slot = {meal.slot: meal.recipe_id for meal in plan.meals}
            for meal in feedback.meals:
                recipe_id = by_slot.get(meal.slot)
                if recipe_id:
                    outcome_score = {"COMPLETED": -1, "SKIPPED": 2, "REPLACED": 3}[meal.outcome]
                    scores[recipe_id] = scores.get(recipe_id, 0) + outcome_score + ((3 - meal.rating) if meal.rating is not None else 0)
                    if meal.outcome == "REPLACED" and meal.replacement_recipe_id:
                        scores[meal.replacement_recipe_id] = scores.get(meal.replacement_recipe_id, 0) - 2
            for directive in feedback.directives:
                scores[directive.recipe_id] = scores.get(directive.recipe_id, 0) + (6 if directive.action == "AVOID" else -6)
        return {key: max(-10, min(10, value)) for key, value in scores.items() if value}

    def delete(self, user_id: str, history_id: str, expected_version: int, idempotency_key: str) -> HistoryCollection:
        return self._delete(user_id, history_id, expected_version, idempotency_key)

    def clear(self, user_id: str, expected_version: int, idempotency_key: str) -> HistoryCollection:
        return self._delete(user_id, None, expected_version, idempotency_key)

    def _delete(self, user_id: str, history_id: str | None, expected_version: int, key: str) -> HistoryCollection:
        operation = f"delete:{history_id}" if history_id else "clear"
        fingerprint = self._fingerprint({"expected_version": expected_version})
        with self._connection() as connection:
            replay = self._replay(connection, user_id, operation, key, fingerprint, HistoryCollection)
            if replay:
                return replay
            row = self._locked_document(connection, user_id)
            if int(row["collection_version"]) != expected_version:
                raise HistoryConflict("stale history collection version")
            items = [AdoptedMealPlan.model_validate(migrate_persisted_document(item)) for item in row["items_json"]]
            removed = items if history_id is None else [item for item in items if item.history_id == history_id]
            if history_id is not None and not removed:
                raise KeyError(history_id)
            kept = [] if history_id is None else [item for item in items if item.history_id != history_id]
            removed_ids = {item.history_id for item in removed}
            feedback = [PlanFeedback.model_validate(item) for item in row["feedback_json"] if item.get("history_id") not in removed_ids]
            deleted_runs = sorted(set(row["deleted_run_ids"]) | {item.original_run_id for item in removed})
            result = HistoryCollection(user_id=user_id, collection_version=expected_version + 1, items=kept)
            self._save_document(connection, user_id, result.collection_version, kept, feedback, deleted_runs)
            self._save_replay(connection, user_id, operation, key, fingerprint, result.model_dump(mode="json"))
            return result

    @staticmethod
    def _locked_document(connection: Any, user_id: str) -> dict[str, Any]:
        connection.execute("INSERT INTO history_documents(user_id) VALUES(%s) ON CONFLICT(user_id) DO NOTHING", (user_id,))
        return connection.execute("SELECT * FROM history_documents WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone()

    @staticmethod
    def _save_document(connection: Any, user_id: str, version: int, items: list, feedback: list, deleted: list[str]) -> None:
        connection.execute(
            """UPDATE history_documents SET collection_version=%s,items_json=%s::jsonb,feedback_json=%s::jsonb,
            deleted_run_ids=%s::jsonb,updated_at=now() WHERE user_id=%s""",
            (version, json.dumps([item.model_dump(mode="json") for item in items]), json.dumps([item.model_dump(mode="json") for item in feedback]), json.dumps(deleted), user_id),
        )

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @staticmethod
    def _replay(connection: Any, user_id: str, operation: str, key: str, fingerprint: str, model: type):
        row = connection.execute("SELECT request_fingerprint,response_json FROM operation_idempotency WHERE user_id=%s AND operation=%s AND idempotency_key=%s", (user_id, operation, key)).fetchone()
        if not row:
            return None
        if row["request_fingerprint"] != fingerprint:
            raise HistoryConflict("idempotency key was already used for a different request")
        return model.model_validate(migrate_persisted_document(row["response_json"]))

    @staticmethod
    def _save_replay(connection: Any, user_id: str, operation: str, key: str, fingerprint: str, response: dict) -> None:
        connection.execute("INSERT INTO operation_idempotency(user_id,operation,idempotency_key,request_fingerprint,response_json) VALUES(%s,%s,%s,%s,%s::jsonb)", (user_id, operation, key, fingerprint, json.dumps(response)))


class PostgresConversationStore(_PostgresBase):
    def delete_all_for_account(self, user_id: str) -> None:
        with self._connection() as connection:
            ids = [row["conversation_id"] for row in connection.execute("SELECT conversation_id FROM conversation_documents WHERE user_id=%s", (user_id,)).fetchall()]
            connection.execute("DELETE FROM conversation_operation_idempotency WHERE scope=%s", (f"create:{user_id}",))
            for conversation_id in ids:
                connection.execute("DELETE FROM conversation_operation_idempotency WHERE scope=%s", (f"message:{conversation_id}",))
            connection.execute("DELETE FROM conversation_operation_idempotency WHERE scope LIKE %s", (f"delete:{user_id}:%",))
            connection.execute("DELETE FROM conversation_documents WHERE user_id=%s", (user_id,))

    def create(self, user_id: str, title: str | None, idempotency_key: str) -> ConversationDetail:
        title = (title or "").strip() or "新对话"
        fingerprint = self._fingerprint({"user_id": user_id, "title": title})
        scope = f"create:{user_id}"
        with self._connection() as connection:
            replay = self._replay(connection, scope, idempotency_key, fingerprint, ConversationDetail)
            if replay:
                return replay
            now = datetime.now(timezone.utc)
            conversation_id = f"conversation-{uuid4().hex}"
            connection.execute("INSERT INTO conversation_documents(conversation_id,user_id,title,created_at,updated_at) VALUES(%s,%s,%s,%s,%s)", (conversation_id, user_id, title, now, now))
            detail = self._detail(connection, user_id, conversation_id)
            self._save_replay(connection, scope, idempotency_key, fingerprint, detail.model_dump(mode="json"))
            return detail

    def list(self, user_id: str, limit: int = 50) -> list[ConversationSummary]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM conversation_documents WHERE user_id=%s ORDER BY updated_at DESC,conversation_id DESC LIMIT %s", (user_id, limit)).fetchall()
        return [self._summary(row) for row in rows]

    def get(self, user_id: str, conversation_id: str) -> ConversationDetail:
        with self._connection() as connection:
            return self._detail(connection, user_id, conversation_id)

    def delete(self, user_id: str, conversation_id: str, expected_version: int, idempotency_key: str) -> None:
        scope = f"delete:{user_id}:{conversation_id}"
        fingerprint = self._fingerprint({"expected_version": expected_version})
        with self._connection() as connection:
            replay = self._replay(connection, scope, idempotency_key, fingerprint, dict)
            if replay is not None:
                return
            row = connection.execute(
                "SELECT conversation_version FROM conversation_documents WHERE user_id=%s AND conversation_id=%s FOR UPDATE",
                (user_id, conversation_id),
            ).fetchone()
            if row is None:
                raise KeyError(conversation_id)
            if int(row["conversation_version"]) != expected_version:
                raise ConversationConflict("conversation changed; refresh and retry")
            connection.execute("DELETE FROM conversation_operation_idempotency WHERE scope=%s", (f"message:{conversation_id}",))
            connection.execute("DELETE FROM conversation_documents WHERE user_id=%s AND conversation_id=%s", (user_id, conversation_id))
            self._save_replay(connection, scope, idempotency_key, fingerprint, {"deleted": True})

    def append_exchange(self, user_id: str, conversation_id: str, expected_version: int, user_content: str, answer: ChatResponse, request_payload: dict, idempotency_key: str) -> ChatResponse:
        scope = f"message:{conversation_id}"
        fingerprint = self._fingerprint(request_payload)
        with self._connection() as connection:
            replay = self._replay(connection, scope, idempotency_key, fingerprint, ChatResponse)
            if replay:
                return replay
            row = connection.execute("SELECT * FROM conversation_documents WHERE user_id=%s AND conversation_id=%s FOR UPDATE", (user_id, conversation_id)).fetchone()
            if not row:
                raise KeyError(conversation_id)
            if int(row["conversation_version"]) != expected_version:
                raise ConversationConflict("conversation changed; refresh and retry")
            now = datetime.now(timezone.utc)
            user_id_message, assistant_id = f"message-{uuid4().hex}", f"message-{uuid4().hex}"
            messages = list(row["messages_json"])
            messages.extend([
                ConversationMessage(message_id=user_id_message, conversation_id=conversation_id, role="user", content=user_content, created_at=now).model_dump(mode="json"),
                ConversationMessage(message_id=assistant_id, conversation_id=conversation_id, role="assistant", content=answer.reply, created_at=now, response_source=answer.response_source, memories_used=answer.memories_used, current_plan_used=answer.current_plan_used, history_plans_used=answer.history_plans_used).model_dump(mode="json"),
            ])
            new_version = expected_version + 1
            title = user_content[:80] if not row["messages_json"] and row["title"] == "新对话" else row["title"]
            connection.execute("UPDATE conversation_documents SET title=%s,conversation_version=%s,messages_json=%s::jsonb,updated_at=%s WHERE conversation_id=%s", (title, new_version, json.dumps(messages, default=str), now, conversation_id))
            persisted = answer.model_copy(update={"conversation_id": conversation_id, "conversation_version": new_version, "user_message_id": user_id_message, "assistant_message_id": assistant_id})
            self._save_replay(connection, scope, idempotency_key, fingerprint, persisted.model_dump(mode="json"))
            return persisted

    def _detail(self, connection: Any, user_id: str, conversation_id: str) -> ConversationDetail:
        row = connection.execute("SELECT * FROM conversation_documents WHERE user_id=%s AND conversation_id=%s", (user_id, conversation_id)).fetchone()
        if not row:
            raise KeyError(conversation_id)
        return ConversationDetail(summary=self._summary(row), messages=[ConversationMessage.model_validate(message) for message in row["messages_json"]])

    @staticmethod
    def _summary(row: dict[str, Any]) -> ConversationSummary:
        return ConversationSummary(conversation_id=row["conversation_id"], user_id=row["user_id"], title=row["title"], conversation_version=row["conversation_version"], created_at=row["created_at"], updated_at=row["updated_at"], message_count=len(row["messages_json"]))

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @staticmethod
    def _replay(connection: Any, scope: str, key: str, fingerprint: str, model: type):
        row = connection.execute("SELECT request_fingerprint,response_json FROM conversation_operation_idempotency WHERE scope=%s AND idempotency_key=%s", (scope, key)).fetchone()
        if not row:
            return None
        if row["request_fingerprint"] != fingerprint:
            raise ConversationConflict("idempotency key was already used for a different request")
        return row["response_json"] if model is dict else model.model_validate(row["response_json"])

    @staticmethod
    def _save_replay(connection: Any, scope: str, key: str, fingerprint: str, response: dict) -> None:
        connection.execute("INSERT INTO conversation_operation_idempotency(scope,idempotency_key,request_fingerprint,response_json) VALUES(%s,%s,%s,%s::jsonb)", (scope, key, fingerprint, json.dumps(response)))
