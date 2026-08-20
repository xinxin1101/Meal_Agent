from __future__ import annotations

import os
import socket
import time
from threading import Event, Thread
from hashlib import sha256

from mealpilot.domain.models import AgentPlanningCommand
from mealpilot.main import (
    _explicit_feedback_scores, _history_recipe_counts, _planning_recipes, durable_runs,
)
from mealpilot.production.queue import OutboxPublisher, PostgresRunQueue
from mealpilot.production.settings import load_production_settings


def _renew_lease(queue: PostgresRunQueue, job_id: int, worker_id: str, stop: Event) -> None:
    while not stop.wait(20):
        if not queue.renew(job_id, worker_id, lease_seconds=60):
            return


def main() -> int:
    settings = load_production_settings()
    if not settings.postgres_enabled or not settings.database_url:
        raise SystemExit("worker requires a PostgreSQL DATABASE_URL")
    worker_id = os.getenv("MEALPILOT_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
    queue = PostgresRunQueue(settings.database_url)
    publisher = OutboxPublisher(settings.database_url, settings.redis_url) if settings.redis_url else None
    while True:
        job = queue.claim(worker_id)
        if job is None:
            if publisher:
                publisher.publish_batch()
            time.sleep(0.5)
            continue
        try:
            heartbeat_stop = Event()
            heartbeat = Thread(
                target=_renew_lease,
                args=(queue, int(job["job_id"]), worker_id, heartbeat_stop),
                daemon=True,
            )
            heartbeat.start()
            record = durable_runs.store.get(job["run_id"])
            command = AgentPlanningCommand.model_validate(record.command)
            durable_runs.execute(
                command.run_id, _planning_recipes(),
                _history_recipe_counts(command.user_id, command.use_history),
                _explicit_feedback_scores(command.user_id, command.use_history),
            )
            heartbeat_stop.set(); heartbeat.join(timeout=2)
            queue.complete(job["job_id"], worker_id)
        except Exception as error:  # errors are deliberately reduced to a non-sensitive fingerprint
            if "heartbeat_stop" in locals():
                heartbeat_stop.set()
            try:
                if durable_runs.store.get(job["run_id"]).status == "CANCELLED":
                    continue
            except KeyError:
                continue
            error_code = f"worker-error-{sha256(type(error).__name__.encode()).hexdigest()[:12]}"
            queue.fail(job["job_id"], worker_id, error_code)
        finally:
            if "heartbeat_stop" in locals():
                heartbeat_stop.set()
            if "heartbeat" in locals():
                heartbeat.join(timeout=2)
        if publisher:
            publisher.publish_batch()


if __name__ == "__main__":
    raise SystemExit(main())
