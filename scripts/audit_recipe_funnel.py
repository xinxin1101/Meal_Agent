"""Recompute the persisted recipe review corpus under the current quality gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from mealpilot.ingestion.funnel import audit_review_corpus
from mealpilot.ingestion.review import ReviewStore
from mealpilot.ingestion.settings import load_recipe_data_paths
from mealpilot.nutrition.loaders import load_food_catalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the real recipe review corpus without mutating it.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--recipe-data-root", type=Path, help="Override MEALPILOT_RECIPE_DATA_ROOT for this run.")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path instead of stdout.")
    parser.add_argument("--fail-on-empty", action="store_true", help="Return exit code 2 when no review records exist.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    project_root = args.project_root.resolve()
    if args.recipe_data_root is not None:
        os.environ["MEALPILOT_RECIPE_DATA_ROOT"] = str(args.recipe_data_root.resolve())
    paths = load_recipe_data_paths(project_root)
    reviews = ReviewStore(paths.reviews).list()
    foods = load_food_catalog(paths.nutrition) if paths.nutrition.exists() else []
    report = audit_review_corpus(reviews, foods)
    payload = report.model_dump(mode="json")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"wrote {args.output.resolve()}")
    if args.fail_on_empty and report.total_reviews == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
