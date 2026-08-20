from uuid import uuid4

from fastapi.testclient import TestClient

from mealpilot.main import app


def test_bootstrap_admin_can_view_catalog_and_regular_user_cannot() -> None:
    admin = TestClient(app)
    login = admin.post("/v1/auth/login", json={"email": "root", "password": "269756"})
    assert login.status_code == 200
    assert login.json()["account"]["role"] == "ADMIN"
    admin.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    catalog = admin.get("/v1/admin/recipes")
    assert catalog.status_code == 200
    assert catalog.json()
    assert all("source_id" in recipe and "cooking_steps" in recipe for recipe in catalog.json())
    assert "ACTIVE_CATALOG" in {recipe["origin"] for recipe in catalog.json()}

    user = TestClient(app)
    registered = user.post("/v1/auth/register", json={
        "email": f"admin-boundary-{uuid4().hex}@example.test",
        "password": "correct-horse-battery-staple",
        "display_name": "regular user",
    })
    assert registered.status_code == 201
    assert registered.json()["account"]["role"] == "USER"
    user.headers["Authorization"] = f"Bearer {registered.json()['access_token']}"
    assert user.get("/v1/admin/recipes").status_code == 403
