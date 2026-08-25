"""Recompute R39 readable/menu/Solver capability reports for stored reviews.

Default mode is read-only. ``--apply`` creates one timestamped backup beside
each changed JSON review before updating its deterministic draft/report.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from mealpilot.ingestion.quality import build_quality_draft
from mealpilot.ingestion.review import RecipeReviewItem, ReviewEvent, ReviewStore
from mealpilot.nutrition.loaders import load_food_catalog


def reclassify_item(item: RecipeReviewItem, foods: list) -> tuple[RecipeReviewItem, bool]:
    draft, report = build_quality_draft(item.raw, item.curation, foods)
    stage = item.processing_stage
    if item.status == "PENDING" and item.llm_assistance is not None:
        stage = "FINAL_VALIDATED" if report.readable_eligible else "FINAL_VALIDATION_BLOCKED"
    changed = draft != item.draft or report != item.quality_report or stage != item.processing_stage
    if not changed:
        return item, False
    event = ReviewEvent(
        event_type="MIGRATED",
        occurred_at=datetime.now(timezone.utc),
        actor="r39-capability-migration",
        reason=f"R39_RECLASSIFIED:readable={report.readable_eligible};status={report.status}",
    )
    return item.model_copy(update={
        "draft": draft,
        "quality_report": report,
        "processing_stage": stage,
        "events": [*item.events, event],
    }), True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, default=Path(".runtime/recipe-data/reviews"))
    parser.add_argument("--nutrition", type=Path, default=Path(".runtime/recipe-data/nutrition/foods.json"))
    parser.add_argument("--apply", action="store_true", help="write changed reviews after creating backups")
    args = parser.parse_args()
    foods = load_food_catalog(args.nutrition) if args.nutrition.exists() else []
    store = ReviewStore(args.reviews)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    changed_count = 0
    for path in sorted(args.reviews.glob("review-mc-*.json")):
        item = RecipeReviewItem.model_validate_json(path.read_text(encoding="utf-8"))
        updated, changed = reclassify_item(item, foods)
        if not changed:
            continue
        changed_count += 1
        print(f"{item.review_id}: readable={updated.quality_report.readable_eligible} status={updated.quality_report.status}")
        if args.apply:
            shutil.copy2(path, path.with_suffix(path.suffix + f".r39-backup-{timestamp}"))
            store.update(updated, expected_version=item.review_version)
    print(f"changed={changed_count} mode={'apply' if args.apply else 'dry-run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
