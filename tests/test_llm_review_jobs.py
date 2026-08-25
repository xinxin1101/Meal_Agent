from pathlib import Path

import mealpilot.ingestion.review as review_module
import pytest
from mealpilot.ingestion.llm_jobs import CreateLlmReviewJobCommand, LlmReviewJobStore, run_llm_review_once
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.review import ReviewRejected
from mealpilot.nutrition.loaders import load_food_catalog
from tests.test_meishichina_quality_review import _raw_recipe


def _service(tmp_path: Path) -> tuple[ReviewService, LlmReviewJobStore]:
    service = ReviewService(ReviewStore(tmp_path / "reviews"), load_food_catalog(Path("data/nutrition/foods.sample.json")))
    return service, LlmReviewJobStore(tmp_path / "jobs.sqlite3")


def test_worker_persists_provider_failure_without_holding_an_http_request(tmp_path: Path, monkeypatch) -> None:
    service, jobs = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="fixture")
    job = jobs.create(item.review_id, CreateLlmReviewJobCommand(expected_review_version=item.review_version), "root", "job-timeout")
    monkeypatch.setattr(review_module, "request_curation_proposal", lambda _raw: (_ for _ in ()).throw(RuntimeError("LLM_PROVIDER_TIMEOUT")))

    assert run_llm_review_once(jobs, service) is True
    completed = jobs.get(job.job_id)
    persisted = service.store.get(item.review_id)
    assert completed.status == "QUEUED"
    assert completed.error_code == "LLM_PROVIDER_TIMEOUT"
    assert completed.attempts == 1
    # Transient failures do not mutate review state before the retry budget is exhausted.
    assert persisted.processing_stage == "INITIAL_VALIDATED"


def test_worker_records_provider_failure_after_bounded_retries(tmp_path: Path, monkeypatch) -> None:
    service, jobs = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="fixture")
    job = jobs.create(item.review_id, CreateLlmReviewJobCommand(expected_review_version=item.review_version), "root", "job-final-timeout")
    monkeypatch.setattr(review_module, "request_curation_proposal", lambda _raw: (_ for _ in ()).throw(RuntimeError("LLM_PROVIDER_TIMEOUT")))

    for _ in range(3):
        assert run_llm_review_once(jobs, service) is True
        # Tests do not wait for wall clock backoff.
        with jobs._connect() as connection:  # noqa: SLF001 - asserts durable retry contract
            connection.execute("UPDATE llm_review_jobs SET next_attempt_at=NULL WHERE job_id=?", (job.job_id,))
            connection.commit()
    completed = jobs.get(job.job_id)
    persisted = service.store.get(item.review_id)
    assert completed.status == "FAILED"
    assert completed.error_code == "LLM_PROVIDER_TIMEOUT"
    assert completed.attempts == 3
    assert persisted.processing_stage == "LLM_FAILED"
    assert persisted.processing_errors == ["LLM_PROVIDER_TIMEOUT"]


def test_worker_marks_stale_review_job_without_overwriting_manual_changes(tmp_path: Path) -> None:
    service, jobs = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="fixture")
    job = jobs.create(item.review_id, CreateLlmReviewJobCommand(expected_review_version=item.review_version), "root", "job-stale")
    service.store.update(item.model_copy(update={"review_version": item.review_version + 1}), expected_version=item.review_version)

    assert run_llm_review_once(jobs, service) is True
    completed = jobs.get(job.job_id)
    assert completed.status == "FAILED"
    assert completed.error_code == "LLM_REVIEW_VERSION_CONFLICT"


def test_migration_warning_blocks_approval(tmp_path: Path) -> None:
    service, _jobs = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="fixture")
    item = service.store.update(item.model_copy(update={
        "processing_stage": "FINAL_VALIDATED",
        "migration_warnings": ["SOURCE_INGREDIENT_COVERAGE_INCOMPLETE"],
        "quality_report": item.quality_report.model_copy(update={"status": "PUBLICATION_READY"}),
    }), expected_version=item.review_version)
    with pytest.raises(ReviewRejected, match="SOURCE_AUDIT_MIGRATION_REVIEW_REQUIRED"):
        service.approve(item.review_id, item.review_version, "root")


def test_worker_marks_review_failed_for_unexpected_exception(tmp_path: Path, monkeypatch) -> None:
    service, jobs = _service(tmp_path)
    item = service.prepare(_raw_recipe(), actor="fixture")
    job = jobs.create(item.review_id, CreateLlmReviewJobCommand(expected_review_version=item.review_version), "root", "job-unexpected")
    monkeypatch.setattr(review_module, "request_curation_proposal", lambda _raw: (_ for _ in ()).throw(TypeError("fixture error")))

    assert run_llm_review_once(jobs, service) is True
    assert jobs.get(job.job_id).error_code == "LLM_REVIEW_EXECUTION_FAILED"
    persisted = service.store.get(item.review_id)
    assert persisted.processing_stage == "LLM_FAILED"
    assert persisted.processing_errors == ["LLM_PROCESSING_FAILED"]
