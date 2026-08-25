import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import mealpilot.main as main
from mealpilot.ingestion.idempotency import ReviewIdempotencyStore
from mealpilot.ingestion.llm_jobs import LlmReviewJobStore
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.nutrition.loaders import load_food_catalog
from tests.test_meishichina_quality_review import _raw_recipe


def admin_client() -> TestClient:
    client = TestClient(main.app)
    session = client.post(
        "/v1/auth/login",
        json={
            "email": os.environ["MEALPILOT_ADMIN_USERNAME"],
            "password": os.environ["MEALPILOT_ADMIN_PASSWORD"],
        },
    )
    assert session.status_code == 200
    client.headers["Authorization"] = f"Bearer {session.json()['access_token']}"
    return client


def test_admin_review_revalidate_and_batch_publish_without_evidence_form(tmp_path: Path, monkeypatch) -> None:
    store = ReviewStore(tmp_path / "reviews")
    service = ReviewService(store, load_food_catalog(Path("data/nutrition/foods.sample.json")))
    item = service.prepare(_raw_recipe(), actor="fixture")
    monkeypatch.setattr(main, "recipe_review_store", store)
    monkeypatch.setattr(main, "recipe_review_idempotency", ReviewIdempotencyStore(tmp_path / "idempotency.sqlite3"))
    monkeypatch.setenv("MEALPILOT_PUBLISHED_RECIPES_PATH", str(tmp_path / "published.json"))
    client = admin_client()

    detail = client.get(f"/v1/admin/reviews/{item.review_id}")
    assert detail.status_code == 200 and detail.json()["can_approve"] is False
    command = {
        "expected_review_version": 0, "servings": "2", "supported_slots": ["lunch", "dinner"], "prep_minutes": 20,
        "ingredients": [
            {"source_index": 0, "raw_name": "牛肉", "canonical_id": "beef", "amount": None, "unit": None, "qualitative_label": None, "nutrition_calculation_role": "INCLUDED"},
            {"source_index": 1, "raw_name": "西兰花", "canonical_id": "broccoli", "amount": None, "unit": None, "qualitative_label": None, "nutrition_calculation_role": "INCLUDED"},
        ],
        "step_overrides": {}, "excluded_step_numbers": [],
    }
    curated = client.put(f"/v1/admin/reviews/{item.review_id}/curation", json=command, headers={"Idempotency-Key": "curate-1"})
    assert curated.status_code == 200
    assert curated.json()["review"]["quality_report"]["status"] == "SOLVER_READY"
    replay = client.put(f"/v1/admin/reviews/{item.review_id}/curation", json=command, headers={"Idempotency-Key": "curate-1"})
    assert replay.status_code == 200 and replay.json()["review"]["review_version"] == 1

    current = store.get(item.review_id)
    store.update(current.model_copy(update={"processing_stage": "FINAL_VALIDATED"}), expected_version=1)
    ready = client.get(f"/v1/admin/reviews/{item.review_id}")
    assert ready.status_code == 200 and ready.json()["can_approve"] is True
    assert client.post(f"/v1/admin/reviews/{item.review_id}/evidence", json={}).status_code == 404
    published = client.post("/v1/admin/reviews/batch-publish", json={
        "items": [{"review_id": item.review_id, "expected_review_version": 2}],
        "dataset_version": "fixture-v1", "confirm_internal_personal_study": True,
    }, headers={"Idempotency-Key": "batch-1"})
    assert published.status_code == 200 and published.json()["published_count"] == 1
    assert store.get(item.review_id).status == "PUBLISHED"
    assert (tmp_path / "published.json").exists()


def test_regular_user_cannot_open_review_workbench(tmp_path: Path, monkeypatch) -> None:
    store = ReviewStore(tmp_path / "reviews")
    item = ReviewService(store, load_food_catalog(Path("data/nutrition/foods.sample.json"))).prepare(_raw_recipe(), actor="fixture")
    monkeypatch.setattr(main, "recipe_review_store", store)
    client = TestClient(main.app)
    registered = client.post("/v1/auth/register", json={"email": f"review-user-{uuid4().hex}@example.test", "password": "correct-horse-battery-staple", "display_name": "review user"})
    client.headers["Authorization"] = f"Bearer {registered.json()['access_token']}"
    assert client.get(f"/v1/admin/reviews/{item.review_id}").status_code == 403


def test_admin_assist_queues_idempotent_background_job(tmp_path: Path, monkeypatch) -> None:
    store = ReviewStore(tmp_path / "reviews")
    service = ReviewService(store, load_food_catalog(Path("data/nutrition/foods.sample.json")))
    item = service.prepare(_raw_recipe(), actor="fixture")
    monkeypatch.setattr(main, "recipe_review_store", store)
    monkeypatch.setattr(main, "recipe_review_idempotency", ReviewIdempotencyStore(tmp_path / "idempotency.sqlite3"))
    monkeypatch.setattr(main, "llm_review_jobs", LlmReviewJobStore(tmp_path / "llm-jobs.sqlite3"))
    client = admin_client()
    headers = {"Idempotency-Key": "assist-provider-failure"}

    queued = client.post(
        f"/v1/admin/reviews/{item.review_id}/assist",
        json={"expected_review_version": item.review_version},
        headers=headers,
    )
    assert queued.status_code == 202
    assert queued.json()["status"] == "QUEUED"
    assert queued.json()["expected_review_version"] == item.review_version
    assert store.get(item.review_id).processing_stage == "INITIAL_VALIDATED"

    replay = client.post(
        f"/v1/admin/reviews/{item.review_id}/assist",
        json={"expected_review_version": item.review_version},
        headers=headers,
    )
    assert replay.status_code == 202
    assert replay.json()["job_id"] == queued.json()["job_id"]

    history = client.get(f"/v1/admin/llm-review-jobs?review_id={item.review_id}")
    assert history.status_code == 200
    assert [job["job_id"] for job in history.json()] == [queued.json()["job_id"]]


def test_admin_can_cancel_queued_llm_job(tmp_path: Path, monkeypatch) -> None:
    store = ReviewStore(tmp_path / "reviews")
    item = ReviewService(store, load_food_catalog(Path("data/nutrition/foods.sample.json"))).prepare(_raw_recipe(), actor="fixture")
    monkeypatch.setattr(main, "recipe_review_store", store)
    monkeypatch.setattr(main, "llm_review_jobs", LlmReviewJobStore(tmp_path / "llm-jobs.sqlite3"))
    client = admin_client()
    queued = client.post(f"/v1/admin/reviews/{item.review_id}/assist", json={"expected_review_version": 0}, headers={"Idempotency-Key": "cancel-me"})
    cancelled = client.post(
        f"/v1/admin/llm-review-jobs/{queued.json()['job_id']}/cancel",
        json={"expected_job_version": queued.json()["job_version"]},
        headers={"Idempotency-Key": "cancel-me-job"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_admin_assist_rejects_stale_review_version_before_queueing(tmp_path: Path, monkeypatch) -> None:
    store = ReviewStore(tmp_path / "reviews")
    service = ReviewService(store, load_food_catalog(Path("data/nutrition/foods.sample.json")))
    item = service.prepare(_raw_recipe(), actor="fixture")
    monkeypatch.setattr(main, "recipe_review_store", store)
    monkeypatch.setattr(main, "recipe_review_idempotency", ReviewIdempotencyStore(tmp_path / "idempotency.sqlite3"))
    monkeypatch.setattr(main, "llm_review_jobs", LlmReviewJobStore(tmp_path / "llm-jobs.sqlite3"))
    client = admin_client()
    response = client.post(
        f"/v1/admin/reviews/{item.review_id}/assist",
        json={"expected_review_version": item.review_version + 1},
        headers={"Idempotency-Key": "assist-provider-timeout"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "REVIEW_VERSION_CONFLICT"
