"""Run the bounded personal-study MeishiChina importer.

Without --execute this command only prints its dry-run plan and performs no
network access. It never downloads recipe images.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mealpilot.ingestion.sources.meishichina.client import FetchPolicy, MeishiChinaHttpClient
from mealpilot.ingestion.sources.meishichina.crawler import crawl_category, stage_batch
from mealpilot.ingestion.settings import load_recipe_data_paths


DEFAULT_CATEGORY_URL = "https://home.meishichina.com/recipe/recai/"
ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATHS = load_recipe_data_paths(ROOT)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category-url", default=DEFAULT_CATEGORY_URL)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--staging-root", type=Path, default=RECIPE_PATHS.raw)
    parser.add_argument(
        "--batch-id",
        default=f"meishichina-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8]}",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-personal-study", action="store_true")
    return parser.parse_args()


def plan(args: argparse.Namespace) -> dict[str, object]:
    if not 1 <= args.limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if not 1 <= args.max_pages <= 3:
        raise ValueError("max_pages must be between 1 and 3")
    FetchPolicy(delay_seconds=args.delay_seconds, timeout_seconds=args.timeout_seconds)
    return {
        "mode": "execute" if args.execute else "dry-run-no-network",
        "category_url": args.category_url,
        "limit": args.limit,
        "max_pages": args.max_pages,
        "delay_seconds": args.delay_seconds,
        "download_images": False,
        "download_comments": False,
        "staging_root": str(args.staging_root.resolve()),
        "batch_id": args.batch_id,
        "publication_eligible": False,
    }


def main() -> int:
    args = parse_args()
    execution_plan = plan(args)
    print(json.dumps(execution_plan, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    if not args.acknowledge_personal_study:
        raise SystemExit("--acknowledge-personal-study is required with --execute")
    policy = FetchPolicy(delay_seconds=args.delay_seconds, timeout_seconds=args.timeout_seconds)
    with MeishiChinaHttpClient(policy=policy) as client:
        result = crawl_category(client, args.category_url, limit=args.limit, max_pages=args.max_pages)
    manifest = stage_batch(
        result.records,
        args.staging_root,
        args.batch_id,
        args.category_url,
        result.category_pages_fetched,
        result.detail_pages_fetched,
        result.duplicate_count,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
