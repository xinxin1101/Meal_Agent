from uuid import uuid4

from fastapi.testclient import TestClient

from mealpilot.main import app


def authenticated_client(label: str = "test") -> tuple[TestClient, str]:
    client = TestClient(app)
    response = client.post("/v1/auth/register", json={
        "email": f"{label}-{uuid4().hex}@example.test",
        "password": "correct-horse-battery-staple",
        "display_name": label,
    })
    assert response.status_code == 201
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return client, response.json()["account"]["user_id"]
