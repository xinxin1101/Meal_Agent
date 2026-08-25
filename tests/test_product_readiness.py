from fastapi.testclient import TestClient

import mealpilot.main as main


def test_product_readiness_requires_nonempty_three_slot_solver_catalog() -> None:
    response = TestClient(main.app).get("/v1/readiness/product")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["solver_eligible_count"] > 0
    assert all(body["per_slot_count"][slot] > 0 for slot in ("breakfast", "lunch", "dinner"))
    assert body["display_recipe_count"] >= 0
    assert body["menu_draft_recipe_count"] >= body["solver_eligible_count"]
    assert body["menu_draft_slot_coverage_complete"] is True


def test_zero_catalog_is_not_business_ready_even_if_infrastructure_is_ready(monkeypatch) -> None:
    monkeypatch.setattr(main, "_recipes", lambda: [])
    infrastructure = TestClient(main.app).get("/ready")
    product = TestClient(main.app).get("/v1/readiness/product")
    assert infrastructure.status_code == 200
    assert product.status_code == 200
    assert product.json()["ready"] is False
    assert "NO_PUBLISHED_RECIPES" in product.json()["reason_codes"]
    assert "BREAKFAST_COVERAGE_MISSING" in product.json()["reason_codes"]
    assert product.json()["display_recipe_count"] == 0
    assert product.json()["menu_draft_recipe_count"] == 0
    assert product.json()["menu_draft_slot_coverage_complete"] is False
