"""Durable, lease-based LLM recipe-review jobs.

The API only queues work.  A standalone worker owns provider calls so a slow
model can never hold an administrator's HTTP request open.
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

from mealpilot.ingestion.llm_assist import LLM_FAILURE_CODES
from mealpilot.ingestion.review import ReviewConflict, ReviewRejected, ReviewService


LlmReviewJobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "ARCHIVED"]
RETRYABLE_LLM_FAILURE_CODES = frozenset({
    "LLM_PROVIDER_CONNECTION_FAILED", "LLM_PROVIDER_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE", "LLM_PROVIDER_RATE_LIMITED",
})
MAX_AUTOMATIC_ATTEMPTS = 3


class CreateLlmReviewJobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_review_version: int = Field(ge=0)


class CancelLlmReviewJobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_job_version: int = Field(ge=0)


class LlmReviewJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    review_id: str
    expected_review_version: int
    status: LlmReviewJobStatus
    job_version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: int = 0
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    result_review_version: int | None = None
    error_code: str | None = None


class LlmReviewJobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int
    job_id: str
    event_type: str
    job_version: int
    created_at: datetime
    payload: dict[str, object] = Field(default_factory=dict)


class LlmReviewJobConflict(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class LlmReviewJobStore:
    """SQLite queue with payload-bound idempotency and expiring leases."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS llm_review_jobs (
                  job_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, expected_review_version INTEGER NOT NULL,
                  status TEXT NOT NULL, job_version INTEGER NOT NULL, created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                  attempts INTEGER NOT NULL, lease_expires_at TEXT, next_attempt_at TEXT, result_review_version INTEGER, error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_llm_review_jobs_claim ON llm_review_jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_llm_review_jobs_review ON llm_review_jobs(review_id, created_at);
                CREATE TABLE IF NOT EXISTS llm_review_job_events (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, event_type TEXT NOT NULL,
                  job_version INTEGER NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_review_job_idempotency (
                  scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL, job_id TEXT NOT NULL,
                  PRIMARY KEY(scope, idempotency_key)
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(llm_review_jobs)").fetchall()}
            if "next_attempt_at" not in columns:
                connection.execute("ALTER TABLE llm_review_jobs ADD COLUMN next_attempt_at TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> LlmReviewJob:
        return LlmReviewJob(
            job_id=row["job_id"], review_id=row["review_id"], expected_review_version=row["expected_review_version"],
            status=row["status"], job_version=row["job_version"], created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            attempts=row["attempts"], lease_expires_at=datetime.fromisoformat(row["lease_expires_at"]) if row["lease_expires_at"] else None,
            next_attempt_at=datetime.fromisoformat(row["next_attempt_at"]) if row["next_attempt_at"] else None,
            result_review_version=row["result_review_version"], error_code=row["error_code"],
        )

    def _event(self, connection: sqlite3.Connection, job_id: str, event_type: str, version: int, payload: dict[str, object] | None = None) -> None:
        connection.execute(
            "INSERT INTO llm_review_job_events(job_id,event_type,job_version,created_at,payload_json) VALUES(?,?,?,?,?)",
            (job_id, event_type, version, _iso(_utcnow()), json.dumps(payload or {}, ensure_ascii=True, sort_keys=True)),
        )

    def create(self, review_id: str, command: CreateLlmReviewJobCommand, actor: str, idempotency_key: str) -> LlmReviewJob:
        fingerprint = hashlib.sha256(f"{review_id}:{command.model_dump_json()}".encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT fingerprint,job_id FROM llm_review_job_idempotency WHERE scope='create' AND idempotency_key=?", (idempotency_key,),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise LlmReviewJobConflict("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
                connection.commit()
                return self.get(prior["job_id"])
            active = connection.execute(
                "SELECT * FROM llm_review_jobs WHERE review_id=? AND status IN ('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1", (review_id,),
            ).fetchone()
            if active:
                active_job = self._from_row(active)
                if active_job.expected_review_version == command.expected_review_version:
                    # A caller may safely retry with a newly generated key while
                    # the same review/version is already in flight.  Persist
                    # that alias so later retries preserve true idempotency.
                    connection.execute(
                        "INSERT INTO llm_review_job_idempotency VALUES('create',?,?,?)",
                        (idempotency_key, fingerprint, active_job.job_id),
                    )
                    connection.commit()
                    return active_job
                connection.commit()
                raise LlmReviewJobConflict("LLM_REVIEW_ALREADY_QUEUED")
            job_id = f"llm-review-{uuid4().hex}"
            connection.execute(
                "INSERT INTO llm_review_jobs(job_id,review_id,expected_review_version,status,job_version,created_by,created_at,updated_at,started_at,completed_at,attempts,lease_expires_at,next_attempt_at,result_review_version,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, review_id, command.expected_review_version, "QUEUED", 0, actor, _iso(now), _iso(now), None, None, 0, None, None, None, None),
            )
            connection.execute("INSERT INTO llm_review_job_idempotency VALUES('create',?,?,?)", (idempotency_key, fingerprint, job_id))
            self._event(connection, job_id, "queued", 0, {"review_id": review_id})
            connection.commit()
            return self.get(job_id)

    def get(self, job_id: str, connection: sqlite3.Connection | None = None) -> LlmReviewJob:
        owns = connection is None
        connection = connection or self._connect()
        try:
            row = connection.execute("SELECT * FROM llm_review_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return self._from_row(row)
        finally:
            if owns:
                connection.close()

    def list(self, review_id: str | None = None, limit: int = 50, include_archived: bool = False) -> list[LlmReviewJob]:
        """Return durable task history for the administrator task centre."""
        bounded_limit = max(1, min(limit, 200))
        with self._connect() as connection:
            if review_id is None:
                archived_clause = "" if include_archived else "WHERE status != 'ARCHIVED'"
                rows = connection.execute(
                    f"SELECT * FROM llm_review_jobs {archived_clause} ORDER BY created_at DESC LIMIT ?", (bounded_limit,),
                ).fetchall()
            else:
                archived_clause = "" if include_archived else "AND status != 'ARCHIVED'"
                rows = connection.execute(
                    f"SELECT * FROM llm_review_jobs WHERE review_id=? {archived_clause} ORDER BY created_at DESC LIMIT ?",
                    (review_id, bounded_limit),
                ).fetchall()
        return [self._from_row(row) for row in rows]

    def claim(self, lease_seconds: int = 300) -> LlmReviewJob | None:
        now = _utcnow()
        lease = now + timedelta(seconds=lease_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM llm_review_jobs WHERE (status='QUEUED' AND (next_attempt_at IS NULL OR next_attempt_at<=?)) OR (status='RUNNING' AND lease_expires_at<?) ORDER BY created_at LIMIT 1", (_iso(now), _iso(now)),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            version = row["job_version"] + 1
            connection.execute(
                "UPDATE llm_review_jobs SET status='RUNNING',job_version=?,updated_at=?,started_at=COALESCE(started_at,?),attempts=attempts+1,lease_expires_at=?,next_attempt_at=NULL,error_code=NULL WHERE job_id=?",
                (version, _iso(now), _iso(now), _iso(lease), row["job_id"]),
            )
            self._event(connection, row["job_id"], "started" if row["status"] == "QUEUED" else "lease_recovered", version)
            connection.commit()
            return self.get(row["job_id"])

    def finish(self, job_id: str, review_version: int) -> LlmReviewJob:
        return self._terminal(job_id, "SUCCEEDED", result_review_version=review_version)

    def fail(self, job_id: str, error_code: str) -> LlmReviewJob:
        return self._terminal(job_id, "FAILED", error_code=error_code)

    def schedule_retry(self, job_id: str, error_code: str) -> bool:
        """Schedule a bounded retry for transient provider failures.

        Returns ``True`` when the same durable job was requeued.  Review state
        remains untouched until the final attempt so OCC stays valid.
        """
        if error_code not in RETRYABLE_LLM_FAILURE_CODES:
            return False
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.get(job_id, connection)
            if current.status != "RUNNING":
                raise LlmReviewJobConflict("JOB_IS_NOT_RUNNING")
            if current.attempts >= MAX_AUTOMATIC_ATTEMPTS:
                connection.commit()
                return False
            delay_seconds = 2 ** current.attempts
            next_attempt = now + timedelta(seconds=delay_seconds)
            version = current.job_version + 1
            connection.execute(
                "UPDATE llm_review_jobs SET status='QUEUED',job_version=?,updated_at=?,lease_expires_at=NULL,next_attempt_at=?,error_code=? WHERE job_id=?",
                (version, _iso(now), _iso(next_attempt), error_code, job_id),
            )
            self._event(connection, job_id, "retry_scheduled", version, {"error_code": error_code, "next_attempt_at": _iso(next_attempt)})
            connection.commit()
        return True

    def cancel(self, job_id: str, expected_job_version: int, idempotency_key: str) -> LlmReviewJob:
        """Cancel only queued work; an in-flight provider call is not safely abortable."""
        fingerprint = hashlib.sha256(f"{job_id}:{expected_job_version}".encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT fingerprint,job_id FROM llm_review_job_idempotency WHERE scope='cancel' AND idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint or prior["job_id"] != job_id:
                    raise LlmReviewJobConflict("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
                connection.commit()
                return self.get(job_id)
            current = self.get(job_id, connection)
            if current.job_version != expected_job_version:
                raise LlmReviewJobConflict("STALE_JOB_VERSION")
            if current.status != "QUEUED":
                raise LlmReviewJobConflict("ONLY_QUEUED_LLM_REVIEW_JOB_CAN_BE_CANCELLED")
            version = current.job_version + 1
            connection.execute(
                "UPDATE llm_review_jobs SET status='CANCELLED',job_version=?,updated_at=?,completed_at=?,lease_expires_at=NULL,next_attempt_at=NULL,error_code='CANCELLED_BY_ADMIN' WHERE job_id=?",
                (version, _iso(now), _iso(now), job_id),
            )
            connection.execute(
                "INSERT INTO llm_review_job_idempotency VALUES('cancel',?,?,?)",
                (idempotency_key, fingerprint, job_id),
            )
            self._event(connection, job_id, "cancelled", version, {"error_code": "CANCELLED_BY_ADMIN"})
            connection.commit()
            return self.get(job_id)

    def _terminal(self, job_id: str, status: Literal["SUCCEEDED", "FAILED"], result_review_version: int | None = None, error_code: str | None = None) -> LlmReviewJob:
        now = _utcnow()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.get(job_id, connection)
            if current.status != "RUNNING":
                raise LlmReviewJobConflict("JOB_IS_NOT_RUNNING")
            version = current.job_version + 1
            connection.execute(
                "UPDATE llm_review_jobs SET status=?,job_version=?,updated_at=?,completed_at=?,lease_expires_at=NULL,next_attempt_at=NULL,result_review_version=?,error_code=? WHERE job_id=?",
                (status, version, _iso(now), _iso(now), result_review_version, error_code, job_id),
            )
            self._event(connection, job_id, status.casefold(), version, {"review_version": result_review_version} if result_review_version is not None else {"error_code": error_code})
            connection.commit()
            return self.get(job_id)


def run_llm_review_once(store: LlmReviewJobStore, review_service: ReviewService, lease_seconds: int = 300) -> bool:
    """Run one queued request and preserve a stable result on both boundaries."""
    job = store.claim(lease_seconds=lease_seconds)
    if job is None:
        return False
    # Avoid spending a provider request on a review that has already changed
    # since it was queued.  This is also the decisive OCC check for retries.
    try:
        current_review = review_service.store.get(job.review_id)
    except KeyError:
        store.fail(job.job_id, "LLM_REVIEW_VERSION_CONFLICT")
        return True
    if current_review.review_version != job.expected_review_version or current_review.status != "PENDING":
        store.fail(job.job_id, "LLM_REVIEW_VERSION_CONFLICT")
        return True
    try:
        item = review_service.assist(job.review_id, job.expected_review_version, job.created_by)
    except ReviewRejected as error:
        code = str(error)
        if code in LLM_FAILURE_CODES:
            if store.schedule_retry(job.job_id, code):
                return True
            try:
                item = review_service.record_llm_failure(job.review_id, job.expected_review_version, job.created_by, code)
            except (ReviewConflict, ReviewRejected):
                store.fail(job.job_id, "LLM_REVIEW_VERSION_CONFLICT")
            else:
                store.fail(job.job_id, code)
        else:
            store.fail(job.job_id, "LLM_REVIEW_VERSION_CONFLICT")
    except ReviewConflict:
        store.fail(job.job_id, "LLM_REVIEW_VERSION_CONFLICT")
    except Exception:
        # Keep the review and task projections consistent even for unexpected
        # worker defects.  The review intentionally receives only a generic,
        # safe-to-display code; detailed exceptions remain process-local.
        if store.schedule_retry(job.job_id, "LLM_REVIEW_EXECUTION_FAILED"):
            return True
        try:
            review_service.record_llm_failure(
                job.review_id, job.expected_review_version, job.created_by, "LLM_PROCESSING_FAILED",
            )
        except (ReviewConflict, ReviewRejected):
            store.fail(job.job_id, "LLM_REVIEW_VERSION_CONFLICT")
        else:
            store.fail(job.job_id, "LLM_REVIEW_EXECUTION_FAILED")
    else:
        store.finish(job.job_id, item.review_version)
    return True
