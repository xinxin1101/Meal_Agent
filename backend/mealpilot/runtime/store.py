import json
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from mealpilot.domain.contract_migration import migrate_persisted_document, migrate_persisted_proposal


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    status: str
    run_version: int
    command: dict[str, Any]
    result: dict[str, Any] | None
    proposal: dict[str, Any] | None


class RunStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = Lock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','PAUSED','COMPLETED','FAILED','CANCELLED')), run_version INTEGER NOT NULL,
                    command_json TEXT NOT NULL, result_json TEXT, proposal_json TEXT,
                    create_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    run_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, response_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    run_version INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            row["run_id"],
            row["status"],
            row["run_version"],
            migrate_persisted_document(json.loads(row["command_json"])),
            migrate_persisted_document(json.loads(row["result_json"])) if row["result_json"] else None,
            migrate_persisted_proposal(json.loads(row["proposal_json"])) if row["proposal_json"] else None,
        )

    def create(self, run_id: str, command: dict[str, Any], idempotency_key: str) -> tuple[RunRecord, bool]:
        with self._lock, self._connection() as connection:
            existing = connection.execute("SELECT * FROM runs WHERE create_key = ?", (idempotency_key,)).fetchone()
            if existing:
                return self._record(existing), False
            connection.execute("INSERT INTO runs(run_id, status, run_version, command_json, create_key) VALUES (?, 'QUEUED', 0, ?, ?)", (run_id, json.dumps(command), idempotency_key))
            self._event(connection, run_id, 0, "queued", {"status": "QUEUED"})
            return self.get(run_id, connection), True

    def get(self, run_id: str, connection: sqlite3.Connection | None = None) -> RunRecord:
        if connection is None:
            with self._connection() as owned:
                return self.get(run_id, owned)
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._record(row)

    def transition(self, run_id: str, expected_version: int, status: str, *, result: dict[str, Any] | None = None, proposal: dict[str, Any] | None = None, event_type: str) -> RunRecord | None:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status=?, run_version=run_version+1, result_json=?, proposal_json=?, updated_at=CURRENT_TIMESTAMP WHERE run_id=? AND run_version=?",
                (status, json.dumps(result) if result else None, json.dumps(proposal) if proposal else None, run_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            record = self.get(run_id, connection)
            self._event(connection, run_id, record.run_version, event_type, {"status": status})
            return record

    def decision_response(self, run_id: str, key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT response_json FROM decisions WHERE run_id=? AND idempotency_key=?", (run_id, key)).fetchone()
            return json.loads(row["response_json"]) if row else None

    def save_decision_response(self, run_id: str, key: str, response: dict[str, Any]) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("INSERT OR IGNORE INTO decisions(run_id, idempotency_key, response_json) VALUES (?, ?, ?)", (run_id, key, json.dumps(response)))

    def events_after(self, run_id: str, event_id: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM run_events WHERE run_id=? AND event_id>? ORDER BY event_id", (run_id, event_id)).fetchall()
            return [{"event_id": row["event_id"], "event_type": row["event_type"], "run_version": row["run_version"], "payload": json.loads(row["payload_json"])} for row in rows]

    def append_event(self, run_id: str, run_version: int, event_type: str, payload: dict[str, Any]) -> None:
        """Persist a replayable, side-effect-free progress event for SSE consumers."""
        with self._lock, self._connection() as connection:
            self._event(connection, run_id, run_version, event_type, payload)

    def records_for_user(self, user_id: str) -> list[RunRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
        return [self._record(row) for row in rows if json.loads(row["command_json"]).get("user_id") == user_id]

    def delete_all_for_account(self, user_id: str) -> None:
        run_ids = [record.run_id for record in self.records_for_user(user_id)]
        with self._lock, self._connection() as connection:
            for run_id in run_ids:
                connection.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM decisions WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM runs WHERE run_id=?", (run_id,))

    def purge_operational_events(self, retention_days: int = 90) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM runs WHERE status IN ('COMPLETED','FAILED','CANCELLED') AND updated_at<?)", (cutoff,))
            return cursor.rowcount

    @staticmethod
    def _event(connection: sqlite3.Connection, run_id: str, version: int, event_type: str, payload: dict[str, Any]) -> None:
        connection.execute("INSERT INTO run_events(run_id, run_version, event_type, payload_json) VALUES (?, ?, ?, ?)", (run_id, version, event_type, json.dumps(payload)))
