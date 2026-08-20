from uuid import uuid4

from fastapi.testclient import TestClient

from mealpilot.main import app
from tests.auth_helpers import authenticated_client


def command(user_id: str, run_id: str) -> dict:
    return {
        "run_id": run_id, "user_id": user_id, "use_history": True,
        "query": "时间 60 分钟，蛋白质 90g，1500-1700 kcal",
        "profile": {"profile_snapshot_id": f"profile-{run_id}", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
        "request": {"request_id": f"request-{run_id}", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
    }


def test_adoption_history_chat_and_hard_delete_form_a_closed_loop(monkeypatch: object) -> None:
    monkeypatch.setattr("mealpilot.memory.chat.load_siliconflow_settings", lambda: type("Settings", (), {"configured": False, "multi_agent_enabled": False})())
    client, user_id = authenticated_client("history")
    unique = uuid4().hex
    run_id = f"run-history-api-{unique}"

    created = client.post("/v1/runs", json=command(user_id, run_id), headers={"Idempotency-Key": f"create-{unique}"})
    assert created.status_code == 202
    completed = client.get(f"/v1/runs/{run_id}").json()
    assert completed["status"] == "COMPLETED"

    adopted = client.post(
        f"/v1/history/{user_id}/adoptions",
        json={"run_id": run_id, "expected_run_version": completed["run_version"]},
        headers={"Idempotency-Key": f"adopt-{unique}"},
    )
    assert adopted.status_code == 201
    history = client.get(f"/v1/history/{user_id}").json()
    assert history["collection_version"] == 1 and len(history["items"]) == 1
    assert history["items"][0]["validation_report"]["valid"] is True

    chat = client.post("/v1/chat", json={"user_id": user_id, "message": "之前哪份计划蛋白质最高？", "use_current_plan": False, "use_history": True, "use_preferences": False})
    assert chat.status_code == 200
    assert len(chat.json()["history_plans_used"]) == 1
    assert "蛋白质最高" in chat.json()["reply"]

    deleted = client.delete(
        f"/v1/history/{user_id}/{adopted.json()['history_id']}?expected_version={history['collection_version']}",
        headers={"Idempotency-Key": f"delete-{unique}"},
    )
    assert deleted.status_code == 200 and deleted.json()["items"] == []
    after_delete = client.post("/v1/chat", json={"user_id": user_id, "message": "回顾历史", "use_history": True, "use_preferences": False})
    assert after_delete.json()["history_plans_used"] == []
    replay = client.post(
        f"/v1/history/{user_id}/adoptions",
        json={"run_id": run_id, "expected_run_version": completed["run_version"]},
        headers={"Idempotency-Key": f"adopt-{unique}"},
    )
    assert replay.status_code == 409


def test_history_adoption_rejects_cross_user_and_unfinished_runs() -> None:
    client, owner = authenticated_client("history-owner")
    unique = uuid4().hex
    run_id = f"run-owner-{unique}"
    completed = client.post("/v1/runs", json=command(owner, run_id), headers={"Idempotency-Key": f"create-owner-{unique}"})
    assert completed.status_code == 202
    snapshot = client.get(f"/v1/runs/{run_id}").json()
    rejected = client.post(
        f"/v1/history/not-the-owner/adoptions",
        json={"run_id": run_id, "expected_run_version": snapshot["run_version"]},
        headers={"Idempotency-Key": f"cross-user-{unique}"},
    )
    assert rejected.status_code == 403
