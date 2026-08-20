"""Migrate legacy recipe review JSON to the mc-r3-v2 contract.

The default is a read-only validation. Use --apply to create a timestamped
backup and atomically replace only PENDING legacy records.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mealpilot.ingestion.quality import RecipeCuration, build_quality_draft
from mealpilot.ingestion.review import RecipeReviewItem, ReviewEvent
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe
from mealpilot.nutrition.loaders import load_food_catalog


ROOT = Path(__file__).resolve().parents[1]


def migrate_payload(payload: dict, foods: list) -> RecipeReviewItem:
    if payload.get("status") != "PENDING":
        raise ValueError("only PENDING legacy reviews can be migrated automatically")
    raw_payload = dict(payload["raw"])
    raw_payload.pop("equipment", None)
    raw_payload["warnings"] = [value for value in raw_payload.get("warnings", []) if value != "EQUIPMENT_NOT_DECLARED"]
    raw_payload["cooking_steps"] = [
        {key: value for key, value in step.items() if key != "equipment_refs"}
        for step in raw_payload.get("cooking_steps", [])
    ]
    raw = RawMeishiChinaRecipe.model_validate(raw_payload)
    legacy = payload.get("curation", {})
    allowed = {
        key: legacy[key]
        for key in (
            "servings", "supported_slots", "prep_minutes", "nutrition_per_serving_override",
            "nutrition_basis", "nutrition_data_version", "ingredient_overrides", "step_overrides",
            "excluded_step_numbers",
        )
        if key in legacy
    }
    curation = RecipeCuration.model_validate(allowed)
    draft, report = build_quality_draft(raw, curation, foods)
    events = [ReviewEvent.model_validate(item) for item in payload.get("events", [])]
    if not events:
        events = [ReviewEvent(event_type="CREATED", occurred_at=payload["created_at"], actor="migration")]
    return RecipeReviewItem(
        review_id=payload["review_id"],
        review_version=int(payload.get("review_version", 0)) + 1,
        created_at=payload["created_at"],
        updated_at=datetime.now(timezone.utc),
        status="PENDING",
        raw=raw,
        curation=curation,
        draft=draft,
        quality_report=report,
        authorization_evidence=payload.get("authorization_evidence", []),
        llm_assistance=None,
        events=events,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", type=Path, default=ROOT / ".runtime" / "recipe-reviews")
    parser.add_argument("--foods", type=Path, default=ROOT / "data" / "nutrition" / "foods.sample.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = sorted(args.review_root.glob("review-mc-*.json"))
    foods = load_food_catalog(args.foods)
    migrated: list[tuple[Path, RecipeReviewItem]] = []
    already_current = 0
    for path in paths:
        raw_text = path.read_text(encoding="utf-8")
        try:
            RecipeReviewItem.model_validate_json(raw_text)
            already_current += 1
        except ValueError:
            migrated.append((path, migrate_payload(json.loads(raw_text), foods)))
    backup = None
    if args.apply and migrated:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ROOT / ".runtime" / "backups" / f"recipe-reviews-v1-{stamp}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(args.review_root, backup)
        for path, item in migrated:
            temporary = path.with_name(f".{path.name}.migration.tmp")
            temporary.write_text(item.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
    print(json.dumps({
        "mode": "applied" if args.apply else "dry-run",
        "records_found": len(paths),
        "records_migrated": len(migrated),
        "already_current": already_current,
        "backup": str(backup) if backup else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
