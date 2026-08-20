from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mealpilot.production.observability import RateLimitMiddleware


def test_production_compose_keeps_data_services_private_and_enables_https() -> None:
    compose = Path("docker-compose.production.yml").read_text(encoding="utf-8")
    caddy = Path("deploy/Caddyfile").read_text(encoding="utf-8")
    migration = Path("migrations/versions/20260813_0001_production_runtime.sql").read_text(encoding="utf-8")
    assert "internal: true" in compose
    assert 'ports: ["80:80", "443:443"' in compose
    assert "FOR UPDATE" not in migration  # locking is an application concern, not a migration side effect
    assert "outbox_events" in migration and "run_jobs" in migration
    assert "maintenance:" in compose and "MEALPILOT_BACKUP_RETENTION_DAYS" in compose
    assert "CANCELLED" in migration
    assert "Strict-Transport-Security" in caddy


def test_rate_limit_returns_429_without_exposing_identity() -> None:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit_per_minute=1, redis_url=None, trust_proxy_headers=False)

    @app.get("/limited")
    def limited() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/limited").status_code == 200
    response = client.get("/limited")
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    assert "testclient" not in response.text
