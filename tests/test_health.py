from fastapi.testclient import TestClient
from uuid import uuid4

from mealpilot.domain.models import AgentPlanningCommand
from mealpilot.main import app, durable_runs
from mealpilot.nutrition.validation import CatalogCoverageReport
from tests.auth_helpers import authenticated_client


def test_health_reports_current_milestone() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "milestone": "frozen-mvp"}


def test_catalog_coverage_endpoint_reports_pinned_snapshot_quality() -> None:
    response = TestClient(app).get("/v1/data-quality/catalog-coverage")
    assert response.status_code == 200
    assert response.json()["complete"] is True
    assert response.json()["recipe_count"] == 5
    assert response.json()["solver_eligible_count"] == 5


def test_preference_memory_can_be_saved_read_and_deleted() -> None:
    client, user_id = authenticated_client("memory")
    memory = {"user_id": user_id, "items": [{"category": "food_preference", "value": "清淡"}]}
    assert client.put(f"/v1/memory/{user_id}", json=memory).status_code == 200
    assert client.get(f"/v1/memory/{user_id}").json() == memory
    assert client.delete(f"/v1/memory/{user_id}").status_code == 204
    assert client.get(f"/v1/memory/{user_id}").json()["items"] == []


def test_chat_returns_a_visible_fallback_reply_without_a_model(monkeypatch: object) -> None:
    monkeypatch.setattr("mealpilot.memory.chat.load_siliconflow_settings", lambda: type("Settings", (), {"configured": False, "multi_agent_enabled": False})())
    client, user_id = authenticated_client("chat")
    response = client.post("/v1/chat", json={"user_id": user_id, "message": "如何调整当前计划？", "plan_context": {"plan_id": "plan-test", "meals": ["breakfast: oats", "lunch: chicken", "dinner: beef"], "totals": {"energy_kcal": "1600", "protein_g": "90", "carbohydrate_g": "150", "fat_g": "50", "prep_minutes": 55}}})
    assert response.status_code == 200
    assert response.json()["response_source"] == "deterministic_fallback"
    assert response.json()["reply"]
    assert "当前已验证计划" in response.json()["reply"]


def test_chat_exposes_exact_memories_used_with_verified_plan(monkeypatch: object) -> None:
    monkeypatch.setattr("mealpilot.memory.chat.load_siliconflow_settings", lambda: type("Settings", (), {"configured": False, "multi_agent_enabled": False})())
    client, user_id = authenticated_client("chat-memory")
    items = [
        {"category": "food_preference", "value": "清淡少油"},
        {"category": "cooking_style", "value": "优先蒸煮"},
    ]
    try:
        saved = client.put(f"/v1/memory/{user_id}", json={"user_id": user_id, "items": items})
        assert saved.status_code == 200
        response = client.post("/v1/chat", json={
            "user_id": user_id,
            "message": "如何把这些偏好融合到当前晚餐？",
            "plan_context": {
                "plan_id": "verified-plan-test",
                "meals": ["早餐：燕麦鸡蛋 × 1.0", "午餐：鸡肉米饭 × 1.0", "晚餐：牛肉时蔬 × 1.0"],
                "totals": {"energy_kcal": "1600", "protein_g": "92", "carbohydrate_g": "150", "fat_g": "48", "prep_minutes": 55},
            },
        })
        assert response.status_code == 200
        assert response.json()["memories_used"] == items
        assert response.json()["response_source"] == "deterministic_fallback"
        assert "当前已验证计划" in response.json()["reply"]
    finally:
        client.delete(f"/v1/memory/{user_id}")


def test_constraint_suggestion_endpoint_returns_only_whitelisted_values(monkeypatch: object) -> None:
    monkeypatch.setattr(
        "mealpilot.main.extract_planning_constraints",
        lambda _query: {
            "max_total_minutes": "35",
            "protein_min_g": "88",
            "energy_kcal_range": "1600-1800",
        },
    )
    client, _ = authenticated_client("suggest")
    response = client.post("/v1/constraint-suggestions", json={"query": "任意自然语言"})
    assert response.status_code == 200
    assert response.json() == {
        "source": "siliconflow",
        "constraints": {
            "max_total_minutes": "35",
            "protein_min_g": "88",
            "energy_kcal_range": "1600-1800",
        },
    }


def test_planning_endpoint_rejects_an_incomplete_healthy_adult_profile() -> None:
    response = authenticated_client("profile-validation")[0].post(
        "/v1/meal-plans/deterministic",
        json={
            "profile": {"profile_snapshot_id": "profile-incomplete", "adult_confirmed": True, "allergens": [], "avoidances": []},
            "request": {"request_id": "request-incomplete", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
        },
    )
    assert response.status_code == 422


def test_planning_endpoint_rejects_removed_price_and_equipment_fields() -> None:
    response = authenticated_client("price-validation")[0].post(
        "/v1/meal-plans/deterministic",
        json={
            "profile": {"profile_snapshot_id": "profile-price", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": [], "equipment": ["stovetop"]},
            "request": {"request_id": "request-price", "max_total_minutes": 60, "max_cost_cny": "45.00", "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "price_snapshot_id": "price-old-v1", "numeric_policy_version": "m1-draft-1"},
        },
    )
    assert response.status_code == 422


def test_planning_endpoint_rejects_an_incomplete_catalog_snapshot(monkeypatch: object) -> None:
    incomplete = CatalogCoverageReport(
        complete=False,
        recipe_count=5,
        solver_eligible_count=5,
        nutrition_data_version="2026-08",
        missing_nutrition_by_recipe={"recipe-oat-egg-v1": ["oats"]},
        unresolved_quantity_by_recipe={},
        publication_only_recipe_ids=[],
    )
    monkeypatch.setattr("mealpilot.main._catalog_coverage", lambda: incomplete)
    response = authenticated_client("catalog-validation")[0].post(
        "/v1/meal-plans/deterministic",
        json={
            "profile": {"profile_snapshot_id": "profile-catalog", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
            "request": {"request_id": "request-catalog", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason_code"] == "CATALOG_COVERAGE_INCOMPLETE"
    assert response.json()["detail"]["catalog_coverage"]["missing_nutrition_by_recipe"] == {"recipe-oat-egg-v1": ["oats"]}


def test_deterministic_planning_endpoint_returns_a_valid_plan() -> None:
    response = authenticated_client("planning")[0].post(
        "/v1/meal-plans/deterministic",
        json={
            "profile": {"profile_snapshot_id": "profile-api", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
            "request": {"request_id": "request-api", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
        },
    )
    assert response.status_code == 200
    assert response.json()["validation_report"]["valid"] is True


def test_agent_run_endpoint_returns_safe_trace_and_plan() -> None:
    response = authenticated_client("agent")[0].post(
        "/v1/agent-runs",
        json={
            "run_id": "run-api", "query": "鐗涜倝椁愶紝鏃堕棿 60 鍒嗛挓锛岃泲鐧借川 90g锛?500-1700 kcal",
            "profile": {"profile_snapshot_id": "profile-agent-api", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
            "request": {"request_id": "request-agent-api", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["result"]["validation_report"]["valid"] is True


def test_pre_m4_in_memory_negotiation_routes_are_removed() -> None:
    client, _ = authenticated_client("negotiation")
    command = {
        "run_id": "run-negotiation-api", "query": "鏃堕棿 1 鍒嗛挓锛岃泲鐧借川 90g锛?500-1700 kcal",
        "profile": {"profile_snapshot_id": "profile-negotiation-api", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
        "request": {"request_id": "request-negotiation-api", "max_total_minutes": 1, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
    }
    assert client.post("/v1/negotiation-runs", json=command).status_code == 404


def test_authenticated_target_suggestion_requires_explicit_calculation_parameter() -> None:
    client, _ = authenticated_client("target-suggestion")
    payload = {"profile_snapshot_id": "target-api", "adult_confirmed": True, "age_years": 26, "nutrition_parameter_sex": "male", "height_cm": "182", "weight_kg": "68", "activity_level": "light", "goal": "maintain", "allergens": [], "avoidances": []}
    response = client.post("/v1/nutrition-target-suggestion", json=payload)
    assert response.status_code == 200
    assert response.json()["requires_user_confirmation"] is True
    payload["nutrition_parameter_sex"] = "unspecified"
    assert client.post("/v1/nutrition-target-suggestion", json=payload).status_code == 422


def test_durable_run_api_can_cancel_a_queued_run() -> None:
    client, _ = authenticated_client("cancel-run")
    unique = uuid4().hex
    command = {
        "run_id": f"run-cancel-api-{unique}", "query": "鏃堕棿 60 鍒嗛挓锛岃泲鐧借川 90g锛?500-1700 kcal",
        "profile": {"profile_snapshot_id": "profile-cancel-api", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
        "request": {"request_id": "request-cancel-api", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
    }
    service = durable_runs
    snapshot, _ = service.create(AgentPlanningCommand.model_validate({**command, "user_id": client.get("/v1/account").json()["user_id"]}), f"cancel-create-{unique}")
    response = client.post(f"/v1/runs/{snapshot.run_id}/cancel", json={"expected_run_version": snapshot.run_version}, headers={"Idempotency-Key": f"cancel-{unique}"})
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"


def test_durable_runtime_api_replays_events_and_rejects_stale_decision() -> None:
    client, _ = authenticated_client("runtime")
    unique = uuid4().hex
    command = {
        "run_id": f"run-runtime-api-{unique}", "query": "鏃堕棿 1 鍒嗛挓锛岃泲鐧借川 90g锛?500-1700 kcal",
        "profile": {"profile_snapshot_id": "profile-runtime-api", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
        "request": {"request_id": "request-runtime-api", "max_total_minutes": 1, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
    }
    queued = client.post("/v1/runs", json=command, headers={"Idempotency-Key": f"runtime-create-key-{unique}"})
    assert queued.status_code == 202
    paused = client.get(f"/v1/runs/{command['run_id']}").json()
    assert paused["status"] == "PAUSED"
    events = client.get(f"/v1/runs/{command['run_id']}/events").text
    assert '"event_type": "queued"' in events and '"event_type": "interrupt"' in events
    audit = client.get(f"/v1/runs/{command['run_id']}/audit")
    assert audit.status_code == 200
    event_types = audit.json()["event_types"]
    assert event_types[:2] == ["queued", "started"] and event_types[-1] == "interrupt"
    assert {"analyze", "retrieve", "solve", "finalize"}.issubset(event_types)
    assert audit.json()["profile_fingerprint"] != command["profile"]["profile_snapshot_id"]
    assert "allergens" not in audit.json()
    stale = client.post(f"/v1/runs/{command['run_id']}/decisions", json={"option_id": paused["proposal"]["options"][0]["option_id"], "expected_run_version": 0}, headers={"Idempotency-Key": "runtime-stale-key"})
    assert stale.status_code == 409
