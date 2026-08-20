from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Any, Iterator

from mealpilot.domain.contract_migration import migrate_persisted_document, migrate_persisted_proposal, migrate_preference_items
from mealpilot.runtime.store import RunRecord


def _psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:  # pragma: no cover - exercised only with production extra
        raise RuntimeError("PostgreSQL mode requires: pip install -e '.[production]'") from error
    return psycopg, dict_row


class PostgresRunStore:
    """PostgreSQL durable run/event store with OCC and transactional Outbox writes."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        psycopg, dict_row = _psycopg()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    @staticmethod
    def _record(row: dict[str, Any]) -> RunRecord:
        return RunRecord(
            str(row["run_id"]), str(row["status"]), int(row["run_version"]),
            migrate_persisted_document(row["command_json"]),
            migrate_persisted_document(row["result_json"]) if row["result_json"] else None,
            migrate_persisted_proposal(row["proposal_json"]) if row["proposal_json"] else None,
        )

    def create(self, run_id: str, command: dict[str, Any], idempotency_key: str) -> tuple[RunRecord, bool]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE create_key=%s", (idempotency_key,)).fetchone()
            if row:
                return self._record(row), False
            row = connection.execute(
                """INSERT INTO runs(run_id,status,run_version,command_json,create_key)
                VALUES(%s,'QUEUED',0,%s::jsonb,%s) RETURNING *""",
                (run_id, json.dumps(command), idempotency_key),
            ).fetchone()
            self._event(connection, run_id, 0, "queued", {"status": "QUEUED"})
            return self._record(row), True

    def get(self, run_id: str, connection: Any | None = None) -> RunRecord:
        if connection is None:
            with self._connection() as owned:
                return self.get(run_id, owned)
        row = connection.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._record(row)

    def transition(self, run_id: str, expected_version: int, status: str, *, result: dict[str, Any] | None = None, proposal: dict[str, Any] | None = None, event_type: str) -> RunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """UPDATE runs SET status=%s,run_version=run_version+1,result_json=%s::jsonb,
                proposal_json=%s::jsonb,updated_at=now() WHERE run_id=%s AND run_version=%s RETURNING *""",
                (status, json.dumps(result) if result is not None else None, json.dumps(proposal) if proposal is not None else None, run_id, expected_version),
            ).fetchone()
            if row is None:
                return None
            record = self._record(row)
            self._event(connection, run_id, record.run_version, event_type, {"status": status})
            return record

    def decision_response(self, run_id: str, key: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT response_json FROM decisions WHERE run_id=%s AND idempotency_key=%s", (run_id, key)).fetchone()
            return row["response_json"] if row else None

    def save_decision_response(self, run_id: str, key: str, response: dict[str, Any]) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO decisions(run_id,idempotency_key,response_json) VALUES(%s,%s,%s::jsonb)
                ON CONFLICT(run_id,idempotency_key) DO NOTHING""",
                (run_id, key, json.dumps(response)),
            )

    def events_after(self, run_id: str, event_id: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_id,event_type,run_version,payload_json FROM run_events WHERE run_id=%s AND event_id>%s ORDER BY event_id",
                (run_id, event_id),
            ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "run_version": row["run_version"], "payload": row["payload_json"]} for row in rows]

    def append_event(self, run_id: str, run_version: int, event_type: str, payload: dict[str, Any]) -> None:
        with self._connection() as connection:
            self._event(connection, run_id, run_version, event_type, payload)

    def records_for_user(self, user_id: str) -> list[RunRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM runs WHERE command_json->>'user_id'=%s ORDER BY created_at", (user_id,)).fetchall()
        return [self._record(row) for row in rows]

    def delete_all_for_account(self, user_id: str) -> None:
        with self._connection() as connection:
            run_ids = [row["run_id"] for row in connection.execute("SELECT run_id FROM runs WHERE command_json->>'user_id'=%s", (user_id,)).fetchall()]
            for run_id in run_ids:
                connection.execute("DELETE FROM outbox_events WHERE aggregate_type='run' AND aggregate_id=%s", (run_id,))
                connection.execute("DELETE FROM run_jobs WHERE run_id=%s", (run_id,))
                connection.execute("DELETE FROM run_events WHERE run_id=%s", (run_id,))
                connection.execute("DELETE FROM decisions WHERE run_id=%s", (run_id,))
                connection.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))

    def purge_operational_events(self, retention_days: int = 90) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM runs WHERE status IN ('COMPLETED','FAILED','CANCELLED') AND updated_at<%s)", (cutoff,))
            return cursor.rowcount

    @staticmethod
    def _event(connection: Any, run_id: str, version: int, event_type: str, payload: dict[str, Any]) -> None:
        event = connection.execute(
            """INSERT INTO run_events(run_id,run_version,event_type,payload_json)
            VALUES(%s,%s,%s,%s::jsonb) RETURNING event_id""",
            (run_id, version, event_type, json.dumps(payload)),
        ).fetchone()
        connection.execute(
            """INSERT INTO outbox_events(aggregate_type,aggregate_id,event_type,payload_json)
            VALUES('run',%s,%s,%s::jsonb)""",
            (run_id, event_type, json.dumps({"event_id": event["event_id"], "run_version": version, "payload": payload})),
        )


class PostgresPreferenceMemoryStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        psycopg, dict_row = _psycopg()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def get(self, user_id: str):
        from mealpilot.domain.models import PreferenceMemory, PreferenceMemoryItem
        with self._connection() as connection:
            row = connection.execute("SELECT items_json FROM preference_memory WHERE user_id=%s", (user_id,)).fetchone()
        items = migrate_preference_items(row["items_json"] if row else [])
        return PreferenceMemory(user_id=user_id, items=[PreferenceMemoryItem.model_validate(item) for item in items])

    def replace(self, memory):
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO preference_memory(user_id,items_json) VALUES(%s,%s::jsonb)
                ON CONFLICT(user_id) DO UPDATE SET items_json=excluded.items_json,updated_at=now()""",
                (memory.user_id, json.dumps([item.model_dump(mode="json") for item in memory.items])),
            )
        return memory

    def delete(self, user_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM preference_memory WHERE user_id=%s", (user_id,))
