"""Run the policy-bound incremental MeishiChina quarantine workflow.

The default is a no-network plan preview. Network access requires both
``--execute`` and ``--acknowledge-personal-study``. Publication remains disabled
by the repository policy unless exact source records are explicitly whitelisted
and already have qualifying hash-bound authorization evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mealpilot.ingestion.automation import SourceAutomationPolicy, run_incremental_automation
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.sources.meishichina.client import FetchPolicy, MeishiChinaHttpClient
from mealpilot.nutrition.loaders import load_food_catalog
from mealpilot.ingestion.settings import load_recipe_data_paths


ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATHS = load_recipe_data_paths(ROOT)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "recipe_sources" / "meishichina.personal-study.json")
    parser.add_argument("--staging-root", type=Path, default=RECIPE_PATHS.raw)
    parser.add_argument("--review-root", type=Path, default=RECIPE_PATHS.reviews)
    parser.add_argument("--state", type=Path, default=RECIPE_PATHS.state)
    parser.add_argument("--published-path", type=Path, default=RECIPE_PATHS.published)
    parser.add_argument("--foods", type=Path, default=ROOT / "data" / "nutrition" / "foods.sample.json")
    parser.add_argument("--batch-id")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-personal-study", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    policy = SourceAutomationPolicy.model_validate_json(args.policy.read_text(encoding="utf-8"))
    preview = {
        "mode": "execute" if args.execute else "dry-run-no-network",
        "policy": policy.model_dump(mode="json"),
        "staging_root": str(args.staging_root.resolve()),
        "review_root": str(args.review_root.resolve()),
        "state_path": str(args.state.resolve()),
        "published_path": str(args.published_path.resolve()),
        "images": False,
        "comments": False,
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    if not args.acknowledge_personal_study:
        raise SystemExit("--acknowledge-personal-study is required with --execute")
    if not policy.enabled:
        raise SystemExit("the source policy is disabled; review and explicitly enable it first")
    review = ReviewService(ReviewStore(args.review_root), load_food_catalog(args.foods))
    fetch = FetchPolicy(delay_seconds=policy.minimum_delay_seconds)
    with MeishiChinaHttpClient(policy=fetch) as client:
        report = run_incremental_automation(policy, client, review, args.staging_root, args.state, args.published_path, batch_id=args.batch_id)
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
