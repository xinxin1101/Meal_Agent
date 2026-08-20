from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mealpilot.domain.models import (
    ChatResponse,
    ConversationDetail,
    ConversationMessage,
    ConversationSummary,
)


class ConversationConflict(Exception):
    """Raised when a stale version or mismatched idempotency key is used."""


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    conversation_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
                ON conversations(user_id, updated_at DESC, conversation_id DESC);
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence_number),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS conversation_idempotency (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY(scope, idempotency_key)
                );
                """
            )

    def create(self, user_id: str, title: str | None, idempotency_key: str) -> ConversationDetail:
        normalized_title = (title or "").strip() or "新对话"
        fingerprint = self._fingerprint({"user_id": user_id, "title": normalized_title})
        scope = f"create:{user_id}"
        with self._lock, self._connection() as connection:
            replay = self._replay(connection, scope, idempotency_key, fingerprint, ConversationDetail)
            if replay is not None:
                return replay
            now = datetime.now(timezone.utc)
            conversation_id = f"conversation-{uuid4().hex}"
            connection.execute(
                "INSERT INTO conversations(conversation_id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (conversation_id, user_id, normalized_title, now.isoformat(), now.isoformat()),
            )
            detail = self._detail(connection, user_id, conversation_id)
            self._save_replay(connection, scope, idempotency_key, fingerprint, detail.model_dump(mode="json"))
            return detail

    def list(self, user_id: str, limit: int = 50) -> list[ConversationSummary]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.*, COUNT(m.message_id) AS message_count
                FROM conversations c
                LEFT JOIN conversation_messages m ON m.conversation_id=c.conversation_id
                WHERE c.user_id=?
                GROUP BY c.conversation_id
                ORDER BY c.updated_at DESC, c.conversation_id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get(self, user_id: str, conversation_id: str) -> ConversationDetail:
        with self._connection() as connection:
            return self._detail(connection, user_id, conversation_id)

    def delete(self, user_id: str, conversation_id: str, expected_version: int, idempotency_key: str) -> None:
        scope = f"delete:{user_id}:{conversation_id}"
        fingerprint = self._fingerprint({"expected_version": expected_version})
        with self._lock, self._connection() as connection:
            replay = self._replay(connection, scope, idempotency_key, fingerprint, dict)
            if replay is not None:
                return
            row = connection.execute(
                "SELECT conversation_version FROM conversations WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchone()
            if row is None:
                raise KeyError(conversation_id)
            if int(row[0]) != expected_version:
                raise ConversationConflict("conversation changed; refresh and retry")
            connection.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conversation_id,))
            connection.execute("DELETE FROM conversation_idempotency WHERE scope=?", (f"message:{conversation_id}",))
            connection.execute("DELETE FROM conversations WHERE user_id=? AND conversation_id=?", (user_id, conversation_id))
            self._save_replay(connection, scope, idempotency_key, fingerprint, {"deleted": True})

    def delete_all_for_account(self, user_id: str) -> None:
        with self._lock, self._connection() as connection:
            conversation_ids = [row[0] for row in connection.execute("SELECT conversation_id FROM conversations WHERE user_id=?", (user_id,)).fetchall()]
            for conversation_id in conversation_ids:
                connection.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conversation_id,))
                connection.execute("DELETE FROM conversation_idempotency WHERE scope=?", (f"message:{conversation_id}",))
            connection.execute("DELETE FROM conversation_idempotency WHERE scope=?", (f"create:{user_id}",))
            connection.execute("DELETE FROM conversation_idempotency WHERE scope LIKE ?", (f"delete:{user_id}:%",))
            connection.execute("DELETE FROM conversations WHERE user_id=?", (user_id,))

    def append_exchange(
        self,
        user_id: str,
        conversation_id: str,
        expected_version: int,
        user_content: str,
        answer: ChatResponse,
        request_payload: dict,
        idempotency_key: str,
    ) -> ChatResponse:
        scope = f"message:{conversation_id}"
        fingerprint = self._fingerprint(request_payload)
        with self._lock, self._connection() as connection:
            replay = self._replay(connection, scope, idempotency_key, fingerprint, ChatResponse)
            if replay is not None:
                return replay
            row = connection.execute(
                "SELECT conversation_version,title FROM conversations WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchone()
            if row is None:
                raise KeyError(conversation_id)
            current_version = int(row[0])
            if current_version != expected_version:
                raise ConversationConflict("conversation changed; refresh and retry")

            sequence = connection.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            now = datetime.now(timezone.utc)
            user_message_id = f"message-{uuid4().hex}"
            assistant_message_id = f"message-{uuid4().hex}"
            user_message = ConversationMessage(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=user_content,
                created_at=now,
            )
            assistant_message = ConversationMessage(
                message_id=assistant_message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=answer.reply,
                created_at=now,
                response_source=answer.response_source,
                memories_used=answer.memories_used,
                current_plan_used=answer.current_plan_used,
                history_plans_used=answer.history_plans_used,
            )
            connection.execute(
                "INSERT INTO conversation_messages(message_id,conversation_id,sequence_number,snapshot_json) VALUES(?,?,?,?)",
                (user_message_id, conversation_id, sequence, user_message.model_dump_json()),
            )
            connection.execute(
                "INSERT INTO conversation_messages(message_id,conversation_id,sequence_number,snapshot_json) VALUES(?,?,?,?)",
                (assistant_message_id, conversation_id, sequence + 1, assistant_message.model_dump_json()),
            )
            title = row[1]
            if sequence == 0 and title == "新对话":
                title = user_content[:80]
            new_version = current_version + 1
            connection.execute(
                "UPDATE conversations SET title=?,conversation_version=?,updated_at=? WHERE conversation_id=?",
                (title, new_version, now.isoformat(), conversation_id),
            )
            persisted = answer.model_copy(update={
                "conversation_id": conversation_id,
                "conversation_version": new_version,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
            })
            self._save_replay(connection, scope, idempotency_key, fingerprint, persisted.model_dump(mode="json"))
            return persisted

    def _detail(self, connection: sqlite3.Connection, user_id: str, conversation_id: str) -> ConversationDetail:
        row = connection.execute(
            """
            SELECT c.*, COUNT(m.message_id) AS message_count
            FROM conversations c
            LEFT JOIN conversation_messages m ON m.conversation_id=c.conversation_id
            WHERE c.user_id=? AND c.conversation_id=?
            GROUP BY c.conversation_id
            """,
            (user_id, conversation_id),
        ).fetchone()
        if row is None:
            raise KeyError(conversation_id)
        messages = connection.execute(
            "SELECT snapshot_json FROM conversation_messages WHERE conversation_id=? ORDER BY sequence_number",
            (conversation_id,),
        ).fetchall()
        return ConversationDetail(
            summary=self._summary(row),
            messages=[ConversationMessage.model_validate_json(message[0]) for message in messages],
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            title=row["title"],
            conversation_version=row["conversation_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"],
        )

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _replay(connection: sqlite3.Connection, scope: str, key: str, fingerprint: str, model: type):
        row = connection.execute(
            "SELECT request_fingerprint,response_json FROM conversation_idempotency WHERE scope=? AND idempotency_key=?",
            (scope, key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ConversationConflict("idempotency key was already used for a different request")
        return json.loads(row[1]) if model is dict else model.model_validate_json(row[1])

    @staticmethod
    def _save_replay(connection: sqlite3.Connection, scope: str, key: str, fingerprint: str, response: dict) -> None:
        connection.execute(
            "INSERT INTO conversation_idempotency(scope,idempotency_key,request_fingerprint,response_json) VALUES(?,?,?,?)",
            (scope, key, fingerprint, json.dumps(response, ensure_ascii=False)),
        )

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
