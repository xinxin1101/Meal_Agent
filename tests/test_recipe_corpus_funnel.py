import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from mealpilot.domain.models import CookingStep
from mealpilot.ingestion.funnel import audit_review_corpus
from mealpilot.ingestion.quality import RecipeCuration
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe, RawRecipeIngredient
from mealpilot.nutrition.loaders import load_food_catalog


FOODS_PATH = Path("data/nutrition/foods.sample.json")


def _raw(source_number: int, *, missing_quantity: bool = False) -> RawMeishiChinaRecipe:
    body = f"m40-f-corpus-{source_number}-{missing_quantity}"
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return RawMeishiChinaRecipe(
        staging_id=f"raw-mc-{content_hash[:24]}",
        captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        source_id=f"meishichina:{source_number}",
        source_url=f"https://home.meishichina.com/recipe-{source_number}.html",
        raw_content_hash=content_hash,
        title=f"测试菜谱 {source_number}",
        ingredients=[
            RawRecipeIngredient(
                group="main",
                raw_name="牛肉",
                raw_amount="" if missing_quantity else "200克",
                raw_text="牛肉" if missing_quantity else "牛肉 200克",
            ),
            RawRecipeIngredient(
                group="secondary",
                raw_name="西兰花",
                raw_amount="100克",
                raw_text="西兰花 100克",
            ),
        ],
        cooking_steps=[
            CookingStep(step_number=1, instruction="牛肉洗净切片。"),
            CookingStep(step_number=2, instruction="放入西兰花炒熟。"),
        ],
        technique="炒",
        source_time_label="廿分钟",
        categories=["午餐"],
    )


def _service(tmp_path: Path) -> ReviewService:
    return ReviewService(ReviewStore(tmp_path / "reviews"), load_food_catalog(FOODS_PATH))


def test_funnel_recomputes_current_gate_and_detects_old_report_drift(tmp_path: Path) -> None:
    service = _service(tmp_path)

    # Remains blocked because servings were never resolved.
    service.prepare(_raw(991001), actor="crawler")

    # Under M40-E this is publication-ready despite a missing source quantity.
    publication = service.prepare(_raw(991002, missing_quantity=True), actor="crawler")
    publication = service.curate(
        publication.review_id,
        RecipeCuration(servings=Decimal("2")),
        expected_version=publication.review_version,
        actor="reviewer",
    )
    assert publication.quality_report.status == "PUBLICATION_READY"

    # Simulate a persisted pre-M40-E report so the audit must not trust it.
    legacy_report = publication.quality_report.model_copy(update={
        "quality_gate_version": "mc-r3-v3",
        "status": "BLOCKED",
        "blocking_reasons": ["INGREDIENT_QUANTITY_INCOMPLETE"],
    })
    publication = service.store.update(
        publication.model_copy(update={"quality_report": legacy_report}),
        expected_version=publication.review_version,
    )

    solver = service.prepare(_raw(991003), actor="crawler")
    solver = service.curate(
        solver.review_id,
        RecipeCuration(servings=Decimal("2")),
        expected_version=solver.review_version,
        actor="reviewer",
    )
    assert solver.quality_report.status == "SOLVER_READY"

    report = audit_review_corpus(
        service.store.list(),
        load_food_catalog(FOODS_PATH),
        generated_at=datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc),
    )

    assert report.quality_gate_version == "mc-r3-v4"
    assert report.total_reviews == 3
    assert report.recomputed_status_counts == {
        "BLOCKED": 1,
        "PUBLICATION_READY": 1,
        "SOLVER_READY": 1,
    }
    assert report.publishable_count == 2
    assert report.publishable_rate == Decimal("0.6667")
    assert report.solver_ready_rate == Decimal("0.3333")
    assert report.solver_within_publishable_rate == Decimal("0.5000")

    assert report.drift_count == 1
    drift = report.drift_records[0]
    assert drift.review_id == publication.review_id
    assert drift.stored_quality_gate_version == "mc-r3-v3"
    assert drift.stored_status == "BLOCKED"
    assert drift.recomputed_status == "PUBLICATION_READY"

    assert report.publication_blockers[0].code == "SERVINGS_MISSING"
    assert report.publication_blockers[0].count == 1
    quantity_blocker = next(item for item in report.solver_blockers if item.code == "NUTRITION_QUANTITY_INCOMPLETE")
    assert quantity_blocker.count == 1
    assert publication.review_id in quantity_blocker.sample_review_ids

    publication_priority = report.priorities[0]
    assert publication_priority.stage == "PUBLICATION"
    assert publication_priority.category == "servings"
    assert publication_priority.remediation == "DETERMINISTIC_OR_LLM_ASSIST"
    assert all(priority.affected_rate <= 1 for priority in report.priorities)

    publication_record = next(item for item in report.records if item.review_id == publication.review_id)
    assert publication_record.recomputed_status == "PUBLICATION_READY"
    assert publication_record.unspecified_quantity_names == ["牛肉"]


def test_funnel_audit_is_read_only(tmp_path: Path) -> None:
    service = _service(tmp_path)
    item = service.prepare(_raw(991010), actor="crawler")
    before = service.store.get(item.review_id).model_dump_json()

    audit_review_corpus(service.store.list(), load_food_catalog(FOODS_PATH))

    after = service.store.get(item.review_id).model_dump_json()
    assert after == before


def test_empty_corpus_has_zero_safe_rates() -> None:
    report = audit_review_corpus([], [])

    assert report.total_reviews == 0
    assert report.recomputed_status_counts == {
        "BLOCKED": 0,
        "PUBLICATION_READY": 0,
        "SOLVER_READY": 0,
    }
    assert report.publishable_rate == 0
    assert report.solver_ready_rate == 0
    assert report.solver_within_publishable_rate == 0
    assert report.publication_blockers == []
    assert report.solver_blockers == []
    assert report.priorities == []
