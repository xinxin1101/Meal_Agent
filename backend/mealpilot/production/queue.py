from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterator

from mealpilot.production.postgres import _psycopg


class PostgresRunQueue:
    """Durable queue with leases; claims use SKIP LOCKED for safe multi-worker use."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        psycopg, dict_row = _psycopg()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def enqueue(self, run_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO run_jobs(run_id) VALUES(%s) ON CONFLICT(run_id) DO NOTHING",
                (run_id,),
            )

    def claim(self, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM run_jobs
                WHERE (status='READY' AND available_at<=now())
                   OR (status='LEASED' AND lease_expires_at<now())
                ORDER BY job_id FOR UPDATE SKIP LOCKED LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            run = connection.execute("SELECT status FROM runs WHERE run_id=%s", (row["run_id"],)).fetchone()
            if run is None or run["status"] == "CANCELLED":
                connection.execute("UPDATE run_jobs SET status='CANCELLED',updated_at=now() WHERE job_id=%s", (row["job_id"],))
                return None
            return connection.execute(
                """UPDATE run_jobs SET status='LEASED',attempts=attempts+1,lease_owner=%s,
                lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                WHERE job_id=%s RETURNING *""",
                (worker_id, lease_seconds, row["job_id"]),
            ).fetchone()

    def complete(self, job_id: int, worker_id: str) -> None:
        with self._connection() as connection:
            updated = connection.execute(
                "UPDATE run_jobs SET status='DONE',lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE job_id=%s AND lease_owner=%s",
                (job_id, worker_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("job lease was lost")

    def renew(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        with self._connection() as connection:
            updated = connection.execute(
                """UPDATE run_jobs SET lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                WHERE job_id=%s AND status='LEASED' AND lease_owner=%s""",
                (lease_seconds, job_id, worker_id),
            )
            return updated.rowcount == 1

    def cancel(self, run_id: str) -> bool:
        with self._connection() as connection:
            updated = connection.execute(
                """UPDATE run_jobs SET status='CANCELLED',lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
                WHERE run_id=%s AND status IN ('READY','LEASED')""",
                (run_id,),
            )
            return updated.rowcount == 1

    def fail(self, job_id: int, worker_id: str, error_code: str, max_attempts: int = 3) -> None:
        with self._connection() as connection:
            row = connection.execute("SELECT attempts FROM run_jobs WHERE job_id=%s AND lease_owner=%s FOR UPDATE", (job_id, worker_id)).fetchone()
            if row is None:
                raise RuntimeError("job lease was lost")
            terminal = int(row["attempts"]) >= max_attempts
            connection.execute(
                """UPDATE run_jobs SET status=%s,last_error=%s,lease_owner=NULL,lease_expires_at=NULL,
                available_at=CASE WHEN %s THEN available_at ELSE now()+interval '5 seconds' END,updated_at=now()
                WHERE job_id=%s""",
                ("FAILED" if terminal else "READY", error_code, terminal, job_id),
            )


class OutboxPublisher:
    """Publish committed Outbox rows to a Redis stream, then mark them delivered."""

    def __init__(self, database_url: str, redis_url: str) -> None:
        self.database_url = database_url
        self.redis_url = redis_url

    def publish_batch(self, limit: int = 100) -> int:
        try:
            import redis
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("Outbox publishing requires the production extra") from error
        psycopg, dict_row = _psycopg()
        published = 0
        client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY outbox_id FOR UPDATE SKIP LOCKED LIMIT %s",
                (limit,),
            ).fetchall()
            for row in rows:
                client.xadd("mealpilot:events", {
                    "outbox_id": str(row["outbox_id"]), "aggregate_type": row["aggregate_type"],
                    "aggregate_id": row["aggregate_id"], "event_type": row["event_type"],
                    "payload": json.dumps(row["payload_json"], ensure_ascii=False),
                }, maxlen=10000, approximate=True)
                connection.execute("UPDATE outbox_events SET published_at=now() WHERE outbox_id=%s", (row["outbox_id"],))
                published += 1
        return published
