"""Migrate legacy recipe-review ingredient overrides to source_index identity.

Default mode is read-only.  ``--apply`` creates a timestamped JSON backup for
every changed review before writing.  Repeated raw names are never guessed:
they remain explicitly marked for administrator review.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from mealpilot.ingestion.quality import normalize_name
from mealpilot.ingestion.review import RecipeReviewItem, ReviewEvent, ReviewStore


def migrate_item(item: RecipeReviewItem) -> tuple[RecipeReviewItem, list[str], bool]:
    positions: dict[str, list[int]] = defaultdict(list)
    for index, ingredient in enumerate(item.raw.ingredients):
        positions[normalize_name(ingredient.raw_name)].append(index)

    warnings: list[str] = []
    overrides = []
    for override in item.curation.ingredient_overrides:
        if override.source_index is not None:
            overrides.append(override)
            continue
        matches = positions.get(normalize_name(override.raw_name), [])
        if len(matches) != 1:
            warnings.append(f"SOURCE_INDEX_AMBIGUOUS:{override.raw_name}")
            overrides.append(override)
            continue
        overrides.append(override.model_copy(update={"source_index": matches[0]}))

    # A record that has never been structured legitimately has zero overrides.
    # Only a partial legacy mapping is an audit gap worth surfacing.
    if overrides and len(overrides) != len(item.raw.ingredients):
        warnings.append("SOURCE_INGREDIENT_COVERAGE_INCOMPLETE")
    if any(value.source_index is None for value in overrides):
        warnings.append("SOURCE_INDEX_MIGRATION_REVIEW_REQUIRED")
    # Clear an earlier false-positive coverage warning after upgrading this
    # script, while preserving any unrelated manual migration warning.
    warnings = list(dict.fromkeys(warnings))
    changed = overrides != item.curation.ingredient_overrides or warnings != item.migration_warnings
    if not changed:
        return item, warnings, False
    curation = item.curation.model_copy(update={"ingredient_overrides": overrides})
    event = ReviewEvent(
        event_type="MIGRATED", occurred_at=datetime.now(timezone.utc), actor="source-index-migration",
        reason=";".join(warnings) if warnings else "SOURCE_INDEX_MIGRATED",
    )
    return item.model_copy(update={"curation": curation, "migration_warnings": warnings, "events": [*item.events, event]}), warnings, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, default=Path(".runtime/recipe-data/reviews"))
    parser.add_argument("--llm-jobs", type=Path, default=Path(".runtime/recipe-data/jobs/llm-reviews.sqlite3"))
    parser.add_argument("--archive-orphaned-jobs", action="store_true", help="archive jobs whose review file no longer exists")
    parser.add_argument("--apply", action="store_true", help="write changes after creating backups")
    args = parser.parse_args()
    store = ReviewStore(args.reviews)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    changed_count = 0
    warning_count = 0
    for path in sorted(args.reviews.glob("review-mc-*.json")):
        item = RecipeReviewItem.model_validate_json(path.read_text(encoding="utf-8"))
        updated, warnings, changed = migrate_item(item)
        if not changed:
            continue
        changed_count += 1
        warning_count += bool(warnings)
        print(f"{item.review_id}: {'WARN ' + ','.join(warnings) if warnings else 'READY'}")
        if args.apply:
            backup = path.with_suffix(path.suffix + f".source-index-backup-{timestamp}")
            shutil.copy2(path, backup)
            store.update(updated, expected_version=item.review_version)
    orphaned = 0
    if args.archive_orphaned_jobs and args.llm_jobs.exists():
        review_ids = {path.stem for path in args.reviews.glob("review-mc-*.json")}
        with sqlite3.connect(args.llm_jobs) as connection:
            rows = connection.execute("SELECT job_id,review_id,job_version FROM llm_review_jobs WHERE status != 'ARCHIVED'").fetchall()
            for job_id, review_id, version in rows:
                if review_id in review_ids:
                    continue
                orphaned += 1
                print(f"{job_id}: ORPHANED_REVIEW")
                if args.apply:
                    now = datetime.now(timezone.utc).isoformat()
                    connection.execute(
                        "UPDATE llm_review_jobs SET status='ARCHIVED',job_version=?,updated_at=?,completed_at=COALESCE(completed_at,?),error_code='ORPHANED_REVIEW' WHERE job_id=?",
                        (version + 1, now, now, job_id),
                    )
                    connection.execute(
                        "INSERT INTO llm_review_job_events(job_id,event_type,job_version,created_at,payload_json) VALUES(?,?,?,?,?)",
                        (job_id, "archived", version + 1, now, json.dumps({"reason_code": "ORPHANED_REVIEW"})),
                    )
            if args.apply:
                connection.commit()
    print(f"changed={changed_count} warnings={warning_count} orphaned_jobs={orphaned} mode={'apply' if args.apply else 'dry-run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
