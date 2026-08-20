"""Standalone recipe acquisition worker.

Run with ``python -m mealpilot.ingestion.worker``. It is intentionally separate
from FastAPI so source latency and failures cannot block planning requests.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from mealpilot.ingestion.acquisition import execute_acquisition_job
from mealpilot.ingestion.jobs import JobConflict, RecipeAcquisitionJobStore
from mealpilot.ingestion.settings import load_recipe_data_paths


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


def main() -> int:
    project_root = Path(os.getenv("MEALPILOT_PROJECT_ROOT", str(Path(__file__).resolve().parents[3]))).resolve()
    paths = load_recipe_data_paths(project_root)
    store = RecipeAcquisitionJobStore(paths.jobs)
    poll_seconds = max(1.0, float(os.getenv("MEALPILOT_RECIPE_WORKER_POLL_SECONDS", "3")))
    while True:
        if not run_once(project_root, store):
            time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
