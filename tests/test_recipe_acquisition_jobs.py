from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mealpilot.main as main

from mealpilot.ingestion.jobs import CreateRecipeAcquisitionJobCommand, JobConflict, RecipeAcquisitionJobStore
from mealpilot.ingestion.worker import run_once, safe_error_code


def command(max_records: int = 3) -> CreateRecipeAcquisitionJobCommand:
    return CreateRecipeAcquisitionJobCommand(
        policy_id="meishichina.personal-study",
        max_records=max_records,
        acknowledge_personal_study=True,
    )


def test_job_creation_is_idempotent_and_payload_bound(tmp_path: Path) -> None:
    store = RecipeAcquisitionJobStore(tmp_path / "jobs.sqlite3")
    first = store.create(command(), "admin@example.test", "same-key")
    replay = store.create(command(), "admin@example.test", "same-key")
    assert replay.job_id == first.job_id
    assert len(store.events(first.job_id)) == 1
    with pytest.raises(JobConflict, match="IDEMPOTENCY_KEY_REUSED"):
        store.create(command(4), "admin@example.test", "same-key")


def test_job_cancel_requires_current_version_and_queued_state(tmp_path: Path) -> None:
    store = RecipeAcquisitionJobStore(tmp_path / "jobs.sqlite3")
    job = store.create(command(), "admin@example.test", "cancel-key")
    with pytest.raises(JobConflict, match="JOB_VERSION_CONFLICT"):
        store.cancel(job.job_id, 9, "stale")
    cancelled = store.cancel(job.job_id, job.job_version, "cancel")
    assert cancelled.status == "CANCELLED"
    assert cancelled.job_version == 1
    assert store.cancel(job.job_id, job.job_version, "cancel").job_version == 1


def test_worker_claims_and_completes_without_network_with_injected_executor(tmp_path: Path) -> None:
    store = RecipeAcquisitionJobStore(tmp_path / "jobs.sqlite3")
    job = store.create(command(), "admin@example.test", "worker-key")

    def fake_executor(_root: Path, _paths: object, claimed: object) -> dict[str, object]:
        assert getattr(claimed, "job_id") == job.job_id
        return {"discovered_count": 2, "new_or_changed_count": 2, "reviews_created": ["review-1", "review-2"]}

    assert run_once(tmp_path, store, executor=fake_executor) is True
    completed = store.get(job.job_id)
    assert completed.status == "REVIEW_READY"
    assert completed.attempts == 1
    assert [event.event_type for event in store.events(job.job_id)] == ["queued", "started", "review_ready"]


def test_worker_redacts_unknown_exception_text() -> None:
    assert safe_error_code(RuntimeError("secret=do-not-log")) == "ACQUISITION_EXECUTION_FAILED"
    assert safe_error_code(RuntimeError("ROBOTS_DENIED")) == "ROBOTS_DENIED"


def test_admin_preview_is_no_network_and_disabled_policy_cannot_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "recipe_acquisition_jobs", RecipeAcquisitionJobStore(tmp_path / "api-jobs.sqlite3"))
    configured = main.load_source_policy(main.PROJECT_ROOT, "meishichina.personal-study")
    monkeypatch.setattr(main, "load_source_policy", lambda _root, _policy_id: configured.model_copy(update={"enabled": False}))
    client = TestClient(main.app)
    session = client.post("/v1/auth/login", json={"email": "root", "password": "269756"})
    client.headers["Authorization"] = f"Bearer {session.json()['access_token']}"
    payload = command().model_dump(mode="json")
    preview = client.post("/v1/admin/acquisition/previews", json=payload)
    assert preview.status_code == 200
    assert preview.json()["executable"] is False
    assert preview.json()["stops"] == ["AUTOMATION_POLICY_DISABLED"]
    queued = client.post("/v1/admin/acquisition/jobs", json=payload, headers={"Idempotency-Key": "disabled-policy"})
    assert queued.status_code == 409
    assert main.recipe_acquisition_jobs.list() == []
