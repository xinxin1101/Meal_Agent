from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mealpilot.domain.models import CookingStep, Recipe
from mealpilot.ingestion.automation import SourceAutomationPolicy, run_incremental_automation
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.sources.meishichina.client import (
    FetchPolicy,
    MeishiChinaHttpClient,
    RobotsDeniedError,
    SourceAccessError,
)
from mealpilot.ingestion.sources.meishichina.crawler import crawl_category, stage_batch
from mealpilot.ingestion.sources.meishichina.parser import (
    AccessChallengeError,
    discover_next_page,
    discover_recipe_urls,
    parse_recipe_page,
)
from mealpilot.nutrition.loaders import load_food_catalog


FIXTURES = Path(__file__).parent / "fixtures" / "meishichina"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_ten_offline_pages_extract_title_ingredients_and_ordered_steps() -> None:
    records = [
        parse_recipe_page(
            fixture(f"recipe_{index:02}.html"),
            f"https://home.meishichina.com/recipe-{900000 + index}.html",
        )
        for index in range(1, 11)
    ]

    assert len({record.title for record in records}) == 10
    assert all(record.ingredients for record in records)
    assert all(record.cooking_steps for record in records)
    assert all(
        [step.step_number for step in record.cooking_steps] == list(range(1, len(record.cooking_steps) + 1))
        for record in records
    )
    assert all(record.license_status == "PENDING" for record in records)
    assert all(record.review_status == "PENDING" for record in records)
    assert all(record.publication_eligible is False for record in records)

    assert "AMBIGUOUS_DURATION" in records[2].warnings
    assert "AUTHOR_MISSING" in records[2].warnings
    assert "IMAGE_DEPENDENT_STEP" in records[3].warnings
    assert "MEDICAL_CLAIM_PRESENT" in records[4].warnings
    assert "COPYRIGHT_RESTRICTION_PRESENT" in records[9].warnings
    assert records[9].title == "杂粮饭示例"
    assert records[9].ingredients[0].group == "main"
    assert records[9].cooking_steps[0].instruction == "大米和小米淘洗干净。"


def test_category_discovery_is_same_site_bounded_and_finds_next_page() -> None:
    html = fixture("category.html")
    page_url = "https://home.meishichina.com/recipe/recai/"
    urls = discover_recipe_urls(html, page_url, limit=10)

    assert len(urls) == 10
    assert urls[0] == "https://home.meishichina.com/recipe-900001.html"
    assert discover_next_page(html, page_url) == "https://home.meishichina.com/recipe/recai/page/2/"
    with pytest.raises(ValueError, match="between 1 and 20"):
        discover_recipe_urls(html, page_url, limit=21)


def test_access_challenge_is_a_hard_parse_stop() -> None:
    with pytest.raises(AccessChallengeError, match="SOURCE_ACCESS_CHALLENGE"):
        parse_recipe_page(
            '<span id="challenge-error-text">Enable JavaScript and cookies to continue</span>',
            "https://home.meishichina.com/recipe-900001.html",
        )


def test_domain_cooking_steps_require_consecutive_numbers() -> None:
    with pytest.raises(ValidationError, match="numbered consecutively"):
        Recipe.model_validate(
            {
                "recipe_id": "r1",
                "version": "1",
                "title": "test",
                "supported_slots": ["lunch"],
                "servings": "1",
                "prep_minutes": 10,
                "nutrition_per_serving": {"energy_kcal": "1", "protein_g": "1", "carbohydrate_g": "1", "fat_g": "1"},
                "nutrition_basis": "CALCULATED_FROM_INGREDIENTS",
                "solver_eligible": True,
                "ingredients": [{"canonical_id": "i1", "canonical_name": "ingredient", "display_quantity": "1克", "quantity_kind": "MEASURED", "amount_g": "1"}],
                "cooking_steps": [CookingStep(step_number=2, instruction="wrong order")],
                "source": {"source_id": "s1", "source_url": "https://example.com/r1", "license": "test", "data_version": "1"},
                "numeric_policy_version": "test",
            }
        )


def mock_client(robots: str = "User-agent: *\nAllow: /\n", *, second_page: bool = False) -> MeishiChinaHttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots, headers={"content-type": "text/plain"})
        if request.url.path == "/recipe/recai/":
            return httpx.Response(200, text=fixture("category.html"), headers={"content-type": "text/html; charset=utf-8"})
        if request.url.path == "/recipe/recai/page/2/" and second_page:
            links = "".join(f'<a href="/recipe-{900000 + index}.html">recipe {index}</a>' for index in range(11, 21))
            return httpx.Response(200, text=f"<html><body>{links}</body></html>", headers={"content-type": "text/html; charset=utf-8"})
        match = request.url.path.removeprefix("/recipe-").removesuffix(".html")
        if match.isdigit() and 900001 <= int(match) <= (900020 if second_page else 900010):
            index = ((int(match) - 900001) % 10) + 1
            html = fixture(f"recipe_{index:02}.html") + f"<!-- source {match} -->"
            return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        transport=transport,
        headers={"User-Agent": "MealPilotRecipeImporter/0.1 (personal-study; no-images)"},
        follow_redirects=True,
    )
    return MeishiChinaHttpClient(
        policy=FetchPolicy(delay_seconds=2.0, timeout_seconds=5.0),
        client=http_client,
        sleep=lambda _: None,
    )


def test_bounded_crawl_stages_ten_pending_records_without_images(tmp_path: Path) -> None:
    client = mock_client()
    result = crawl_category(client, "https://home.meishichina.com/recipe/recai/", limit=10)

    assert len(result.records) == 10
    assert result.category_pages_fetched == 1
    assert result.detail_pages_fetched == 10
    manifest = stage_batch(
        result.records,
        tmp_path,
        "meishichina-test-batch",
        "https://home.meishichina.com/recipe/recai/",
        result.category_pages_fetched,
        result.detail_pages_fetched,
        result.duplicate_count,
    )
    assert manifest["imported_count"] == 10
    assert manifest["image_download_count"] == 0
    assert manifest["batch_status"] == "RAW_QUARANTINED"
    assert manifest["publication_eligible"] is False
    records = [json.loads(line) for line in (tmp_path / "meishichina-test-batch" / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(record["license_status"] == "PENDING" for record in records)
    assert all(record["review_status"] == "PENDING" for record in records)
    assert all("cooking_steps" in record for record in records)
    assert all("image" not in record for record in records)


def test_incremental_crawl_skips_known_first_page_and_discovers_next_page() -> None:
    result = crawl_category(
        mock_client(second_page=True),
        "https://home.meishichina.com/recipe/recai/",
        limit=10,
        max_pages=2,
        known_source_ids={f"meishichina:{900000 + index}" for index in range(1, 11)},
    )
    assert result.category_pages_fetched == 2
    assert result.detail_pages_fetched == 10
    assert [item.source_id for item in result.records] == [f"meishichina:{900000 + index}" for index in range(11, 21)]


def test_robots_denial_and_cloudflare_challenge_stop_fetching() -> None:
    denied_client = mock_client("User-agent: *\nDisallow: /\n")
    with pytest.raises(RobotsDeniedError, match="ROBOTS_DENIED"):
        denied_client.get_html("https://home.meishichina.com/recipe/recai/")

    def challenge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            text='<span id="challenge-error-text">Enable JavaScript and cookies to continue</span>',
            headers={"content-type": "text/html"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(challenge_handler), follow_redirects=True)
    challenge_client = MeishiChinaHttpClient(client=http_client, sleep=lambda _: None)
    with pytest.raises(SourceAccessError, match="SOURCE_ACCESS_CHALLENGE"):
        challenge_client.get_html("https://home.meishichina.com/recipe/recai/")


def test_incremental_automation_stages_changes_once_and_requires_admin_batch_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("mealpilot.ingestion.review.request_curation_proposal", lambda _raw: (_ for _ in ()).throw(RuntimeError("SILICONFLOW_NOT_CONFIGURED")))
    policy = SourceAutomationPolicy.model_validate(
        {
            "policy_id": "meishichina.test",
            "policy_version": "1",
            "enabled": True,
            "category_urls": ["https://home.meishichina.com/recipe/recai/"],
            "max_records_per_run": 2,
            "auto_publish": True,
            "auto_publish_source_ids": ["meishichina:900001", "meishichina:900002"],
        }
    )
    review = ReviewService(
        ReviewStore(tmp_path / "reviews"),
        load_food_catalog(Path("data/nutrition/foods.sample.json")),
    )
    first = run_incremental_automation(
        policy,
        mock_client(),
        review,
        tmp_path / "raw",
        tmp_path / "state.json",
        tmp_path / "published.json",
        batch_id="meishichina-auto-first",
    )
    assert first.new_or_changed_count == 2
    assert len(first.reviews_created) == 2
    assert first.auto_published_recipe_ids == []
    assert all(reasons == ["ADMIN_BATCH_REVIEW_REQUIRED"] for reasons in first.publication_stops.values())
    assert first.processing_counts["LLM_FAILED"] == 2

    second = run_incremental_automation(
        policy,
        mock_client(),
        review,
        tmp_path / "raw",
        tmp_path / "state.json",
        tmp_path / "published.json",
    )
    assert second.new_or_changed_count == 2
    assert second.unchanged_count == 0
    assert len(second.reviews_created) == 2
    assert second.batch_path is not None
    assert set(second.reviews_created).isdisjoint(first.reviews_created)
