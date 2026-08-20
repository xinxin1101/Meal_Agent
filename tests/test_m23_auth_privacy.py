from uuid import uuid4

from fastapi.testclient import TestClient

from mealpilot.main import app


def register_client(label: str) -> tuple[TestClient, dict]:
    client = TestClient(app)
    email = f"{label}-{uuid4().hex}@example.test"
    response = client.post("/v1/auth/register", json={"email": email, "password": "correct-horse-battery-staple", "display_name": label})
    assert response.status_code == 201
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return client, response.json()


def planning_command(user_id: str, run_id: str) -> dict:
    return {
        "run_id": run_id, "user_id": user_id, "query": "60 minutes, protein 90g, 1500-1700 kcal",
        "profile": {"profile_snapshot_id": f"profile-{run_id}", "adult_confirmed": True, "age_years": 28, "nutrition_parameter_sex": "unspecified", "height_cm": "170", "weight_kg": "65", "activity_level": "moderate", "goal": "maintain", "allergens": [], "avoidances": []},
        "request": {"request_id": f"request-{run_id}", "max_total_minutes": 60, "energy_kcal_range": {"min": "1500", "max": "1700"}, "protein_min_g": "90", "numeric_policy_version": "contract-v2-decimal-v1"},
    }


def test_authentication_refresh_rotation_logout_and_duplicate_email() -> None:
    client, session = register_client("auth")
    assert session["token_type"] == "bearer" and session["expires_in"] == 900
    assert client.get("/v1/account").json()["user_id"] == session["account"]["user_id"]
    refresh = client.post("/v1/auth/refresh")
    assert refresh.status_code == 200 and refresh.json()["access_token"] != session["access_token"]
    client.headers["Authorization"] = f"Bearer {refresh.json()['access_token']}"
    assert client.post("/v1/auth/register", json={"email": session["account"]["email"], "password": "correct-horse-battery-staple", "display_name": "duplicate"}).status_code == 409
    assert client.post("/v1/auth/logout").status_code == 204
    del client.headers["Authorization"]
    assert client.get("/v1/account").status_code == 401


def test_user_scoped_resources_and_run_ids_reject_cross_account_access() -> None:
    owner, owner_session = register_client("owner")
    attacker, attacker_session = register_client("attacker")
    owner_id = owner_session["account"]["user_id"]
    attacker_id = attacker_session["account"]["user_id"]
    memory = {"user_id": owner_id, "items": [{"category": "food_preference", "value": "light"}]}
    assert owner.put(f"/v1/memory/{owner_id}", json=memory).status_code == 200
    assert attacker.get(f"/v1/memory/{owner_id}").status_code == 403
    assert attacker.put(f"/v1/memory/{owner_id}", json=memory).status_code == 403
    run_id = f"m23-{uuid4().hex}"
    created = owner.post("/v1/runs", json=planning_command(owner_id, run_id), headers={"Idempotency-Key": run_id})
    assert created.status_code == 202
    assert attacker.get(f"/v1/runs/{run_id}").status_code == 403
    assert attacker.get(f"/v1/runs/{run_id}/audit").status_code == 403
    wrong_body = planning_command(owner_id, f"wrong-{uuid4().hex}")
    assert attacker.post("/v1/runs", json=wrong_body, headers={"Idempotency-Key": uuid4().hex}).status_code == 403
    assert attacker.post("/v1/chat", json={"user_id": owner_id, "message": "show data"}).status_code == 403
    assert owner.get(f"/v1/history/{attacker_id}").status_code == 403


def test_server_profile_export_and_account_deletion() -> None:
    client, session = register_client("privacy")
    user_id = session["account"]["user_id"]
    profile = {"profile_snapshot_id": "profile-private", "adult_confirmed": True, "age_years": 30, "nutrition_parameter_sex": "unspecified", "height_cm": "171", "weight_kg": "66", "activity_level": "light", "goal": "maintain", "allergens": [], "avoidances": []}
    saved = client.put("/v1/account/profile", json={"expected_profile_version": 0, "profile": profile})
    assert saved.status_code == 200 and saved.json()["profile_version"] == 1
    assert client.put("/v1/account/profile", json={"expected_profile_version": 0, "profile": profile}).status_code == 409
    exported = client.get("/v1/account/export")
    assert exported.status_code == 200
    assert exported.json()["account"]["user_id"] == user_id
    assert "password" not in str(exported.json()).casefold()
    deleted = client.request("DELETE", "/v1/account", json={"password": "correct-horse-battery-staple", "confirmation": "DELETE MY ACCOUNT"})
    assert deleted.status_code == 204
    assert client.get("/v1/account").status_code == 401
    login = client.post("/v1/auth/login", json={"email": session["account"]["email"], "password": "correct-horse-battery-staple"})
    assert login.status_code == 401
