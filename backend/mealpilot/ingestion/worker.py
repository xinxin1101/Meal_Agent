"""Standalone recipe acquisition worker.

Run with ``python -m mealpilot.ingestion.worker``. It is intentionally separate
from FastAPI so source latency and failures cannot block planning requests.
"""

from __future__ import annotations

import os
import json
import socket
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

from mealpilot.ingestion.acquisition import execute_acquisition_job
from mealpilot.ingestion.jobs import JobConflict, RecipeAcquisitionJobStore
from mealpilot.ingestion.llm_jobs import LlmReviewJobStore, run_llm_review_once
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.settings import load_recipe_data_paths
from mealpilot.llm.siliconflow import load_siliconflow_settings
from mealpilot.nutrition.loaders import load_food_catalog


SAFE_ERRORS = {
    "AUTOMATION_POLICY_DISABLED", "CLIENT_RATE_LIMIT_IS_WEAKER_THAN_SOURCE_POLICY",
    "ROBOTS_DENIED", "SOURCE_NETWORK_ERROR", "SOURCE_ACCESS_CHALLENGE",
    "SOURCE_RESPONSE_TOO_LARGE", "SOURCE_CONTENT_TYPE_INVALID", "SOURCE_ENCODING_INVALID",
}


def safe_error_code(error: Exception) -> str:
    value = str(error)
    if value in SAFE_ERRORS or value.startswith("SOURCE_ACCESS_STOP_") or value.startswith("SOURCE_HTTP_"):
        return value
    return "ACQUISITION_EXECUTION_FAILED"


def run_once(project_root: Path, store: RecipeAcquisitionJobStore, executor=execute_acquisition_job) -> bool:
    job = store.claim(lease_seconds=int(os.getenv("MEALPILOT_RECIPE_JOB_LEASE_SECONDS", "900")))
    if job is None:
        return False
    try:
        result = executor(project_root, load_recipe_data_paths(project_root), job)
        changed = int(result.get("new_or_changed_count", 0))
        status = "REVIEW_READY" if changed > 0 else "PARTIAL"
        store.finish(job.job_id, status, result)
    except Exception as error:  # worker boundary records a stable, redacted failure code
        try:
            store.fail(job.job_id, safe_error_code(error))
        except JobConflict:
            pass
    return True


def _write_status(
    path: Path,
    state: str,
    last_llm_error_code: str | None = None,
    last_llm_job_id: str | None = None,
    last_llm_status: str | None = None,
    last_llm_finished_at: str | None = None,
) -> None:
    """Publish a secret-free Worker heartbeat for the administrator console."""
    settings = load_siliconflow_settings()
    host = urlparse(settings.base_url).hostname
    dns_status = "NOT_CONFIGURED" if not settings.configured else "UNKNOWN"
    if settings.configured and host:
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            dns_status = "RESOLVED"
        except OSError:
            dns_status = "UNRESOLVED"
    payload = {
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "provider_configured": settings.configured,
        "provider_host": host,
        "dns_status": dns_status,
        "last_llm_error_code": last_llm_error_code,
        "last_llm_job_id": last_llm_job_id,
        "last_llm_status": last_llm_status,
        "last_llm_finished_at": last_llm_finished_at,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    project_root = Path(os.getenv("MEALPILOT_PROJECT_ROOT", str(Path(__file__).resolve().parents[3]))).resolve()
    paths = load_recipe_data_paths(project_root)
    store = RecipeAcquisitionJobStore(paths.jobs)
    llm_store = LlmReviewJobStore(paths.llm_jobs)
    foods = load_food_catalog(paths.nutrition) if paths.nutrition.exists() else []
    review_service = ReviewService(ReviewStore(paths.reviews), foods)
    poll_seconds = max(1.0, float(os.getenv("MEALPILOT_RECIPE_WORKER_POLL_SECONDS", "3")))
    _write_status(paths.worker_status, "IDLE")
    while True:
        _write_status(paths.worker_status, "RUNNING")
        ran_acquisition = run_once(project_root, store)
        ran_llm_review = run_llm_review_once(
            llm_store,
            review_service,
            lease_seconds=int(os.getenv("MEALPILOT_LLM_REVIEW_JOB_LEASE_SECONDS", "300")),
        )
        latest = llm_store.list(limit=1)
        current = latest[0] if latest else None
        last_error = current.error_code if current and current.status in {"FAILED", "QUEUED"} else None
        _write_status(
            paths.worker_status,
            "IDLE" if not ran_acquisition and not ran_llm_review else "RUNNING",
            last_error,
            current.job_id if current else None,
            current.status if current else None,
            current.completed_at.isoformat() if current and current.completed_at else None,
        )
        if not ran_acquisition and not ran_llm_review:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
