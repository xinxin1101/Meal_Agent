"""Durable, lease-based recipe acquisition jobs.

The API may enqueue and observe jobs. Only the standalone worker may claim and
execute them. Job payloads contain reviewed policy identifiers, never arbitrary
source URLs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal["QUEUED", "RUNNING", "REVIEW_READY", "PARTIAL", "FAILED", "CANCELLED"]


class CreateRecipeAcquisitionJobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = Field(min_length=3, max_length=80)
    max_records: int = Field(default=10, ge=1, le=20)
    acknowledge_personal_study: Literal[True]


class CancelRecipeAcquisitionJobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_job_version: int = Field(ge=0)


class RecipeAcquisitionJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    policy_id: str
    max_records: int
    status: JobStatus
    job_version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: int = 0
    lease_expires_at: datetime | None = None
    result: dict[str, object] | None = None
    error_code: str | None = None


class RecipeAcquisitionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int
    job_id: str
    event_type: str
    job_version: int
    created_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)


class SourcePolicySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    policy_version: str
    enabled: bool
    purpose: Literal["PERSONAL_STUDY"]
    max_records_per_run: int
    max_pages_per_category: int
    minimum_delay_seconds: str
    category_count: int
    images: Literal[False] = False
    comments: Literal[False] = False
    auto_publish: Literal[False] = False


class RecipeAcquisitionPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: SourcePolicySummary
    requested_max_records: int
    executable: bool
    stops: list[str]
    destination: Literal["RAW_QUARANTINE_AND_REVIEW_QUEUE"] = "RAW_QUARANTINE_AND_REVIEW_QUEUE"


class JobConflict(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class RecipeAcquisitionJobStore:
    """SQLite queue with idempotent creation, OCC and expiring worker leases."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS acquisition_jobs (
                  job_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, max_records INTEGER NOT NULL,
                  status TEXT NOT NULL, job_version INTEGER NOT NULL, created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                  completed_at TEXT, attempts INTEGER NOT NULL, lease_expires_at TEXT,
                  result_json TEXT, error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS acquisition_events (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                  event_type TEXT NOT NULL, job_version INTEGER NOT NULL,
                  created_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS acquisition_idempotency (
                  scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                  job_id TEXT NOT NULL, PRIMARY KEY(scope, idempotency_key)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> RecipeAcquisitionJob:
        return RecipeAcquisitionJob(
            job_id=row["job_id"], policy_id=row["policy_id"], max_records=row["max_records"],
            status=row["status"], job_version=row["job_version"], created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            attempts=row["attempts"], lease_expires_at=datetime.fromisoformat(row["lease_expires_at"]) if row["lease_expires_at"] else None,
            result=json.loads(row["result_json"]) if row["result_json"] else None, error_code=row["error_code"],
        )

    def _event(self, connection: sqlite3.Connection, job_id: str, event_type: str, version: int, payload: dict[str, object] | None = None) -> None:
        connection.execute(
            "INSERT INTO acquisition_events(job_id,event_type,job_version,created_at,payload_json) VALUES(?,?,?,?,?)",
            (job_id, event_type, version, _iso(_utcnow()), json.dumps(payload or {}, ensure_ascii=True, sort_keys=True)),
        )

    def create(self, command: CreateRecipeAcquisitionJobCommand, actor: str, idempotency_key: str) -> RecipeAcquisitionJob:
        fingerprint = hashlib.sha256(command.model_dump_json().encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT fingerprint,job_id FROM acquisition_idempotency WHERE scope='create' AND idempotency_key=?", (idempotency_key,),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise JobConflict("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
                return self.get(prior["job_id"], connection)
            job_id = f"acq-{uuid4().hex}"
            connection.execute(
                "INSERT INTO acquisition_jobs(job_id,policy_id,max_records,status,job_version,created_by,created_at,updated_at,started_at,completed_at,attempts,lease_expires_at,result_json,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, command.policy_id, command.max_records, "QUEUED", 0, actor, _iso(now), _iso(now), None, None, 0, None, None, None),
            )
            connection.execute("INSERT INTO acquisition_idempotency VALUES('create',?,?,?)", (idempotency_key, fingerprint, job_id))
            self._event(connection, job_id, "queued", 0, {"max_records": command.max_records, "policy_id": command.policy_id})
            connection.commit()
            return self.get(job_id, connection)

    def get(self, job_id: str, connection: sqlite3.Connection | None = None) -> RecipeAcquisitionJob:
        owns = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute("SELECT * FROM acquisition_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return self._from_row(row)
        finally:
            if owns:
                connection.close()

    def list(self, limit: int = 50) -> list[RecipeAcquisitionJob]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM acquisition_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._from_row(row) for row in rows]

    def events(self, job_id: str, after: int = 0) -> list[RecipeAcquisitionEvent]:
        self.get(job_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM acquisition_events WHERE job_id=? AND event_id>? ORDER BY event_id", (job_id, after)).fetchall()
        return [RecipeAcquisitionEvent(event_id=row["event_id"], job_id=row["job_id"], event_type=row["event_type"], job_version=row["job_version"], created_at=datetime.fromisoformat(row["created_at"]), payload=json.loads(row["payload_json"])) for row in rows]

    def claim(self, lease_seconds: int = 300) -> RecipeAcquisitionJob | None:
        now = _utcnow()
        lease = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM acquisition_jobs WHERE status='QUEUED' OR (status='RUNNING' AND lease_expires_at<?) ORDER BY created_at LIMIT 1", (_iso(now),),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            version = row["job_version"] + 1
            connection.execute(
                "UPDATE acquisition_jobs SET status='RUNNING',job_version=?,updated_at=?,started_at=COALESCE(started_at,?),attempts=attempts+1,lease_expires_at=?,error_code=NULL WHERE job_id=?",
                (version, _iso(now), _iso(now), _iso(lease), row["job_id"]),
            )
            self._event(connection, row["job_id"], "started" if row["status"] == "QUEUED" else "lease_recovered", version)
            connection.commit()
            return self.get(row["job_id"], connection)

    def finish(self, job_id: str, status: Literal["REVIEW_READY", "PARTIAL"], result: dict[str, object]) -> RecipeAcquisitionJob:
        return self._terminal(job_id, status, result=result)

    def fail(self, job_id: str, error_code: str) -> RecipeAcquisitionJob:
        return self._terminal(job_id, "FAILED", error_code=error_code)

    def _terminal(self, job_id: str, status: JobStatus, result: dict[str, object] | None = None, error_code: str | None = None) -> RecipeAcquisitionJob:
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.get(job_id, connection)
            if current.status != "RUNNING":
                raise JobConflict("JOB_IS_NOT_RUNNING")
            version = current.job_version + 1
            connection.execute(
                "UPDATE acquisition_jobs SET status=?,job_version=?,updated_at=?,completed_at=?,lease_expires_at=NULL,result_json=?,error_code=? WHERE job_id=?",
                (status, version, _iso(now), _iso(now), json.dumps(result, ensure_ascii=True, sort_keys=True) if result is not None else None, error_code, job_id),
            )
            self._event(connection, job_id, status.casefold(), version, {"error_code": error_code} if error_code else result)
            connection.commit()
            return self.get(job_id, connection)

    def cancel(self, job_id: str, expected_version: int, idempotency_key: str) -> RecipeAcquisitionJob:
        now = _utcnow()
        scope = f"cancel:{job_id}"
        fingerprint = hashlib.sha256(str(expected_version).encode("ascii")).hexdigest()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT fingerprint,job_id FROM acquisition_idempotency WHERE scope=? AND idempotency_key=?", (scope, idempotency_key),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise JobConflict("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
                return self.get(job_id, connection)
            current = self.get(job_id, connection)
            if current.job_version != expected_version:
                raise JobConflict("JOB_VERSION_CONFLICT")
            if current.status != "QUEUED":
                raise JobConflict("ONLY_QUEUED_JOBS_CAN_BE_CANCELLED")
            version = current.job_version + 1
            connection.execute("UPDATE acquisition_jobs SET status='CANCELLED',job_version=?,updated_at=?,completed_at=? WHERE job_id=?", (version, _iso(now), _iso(now), job_id))
            connection.execute("INSERT INTO acquisition_idempotency VALUES(?,?,?,?)", (scope, idempotency_key, fingerprint, job_id))
            self._event(connection, job_id, "cancelled", version)
            connection.commit()
            return self.get(job_id, connection)
