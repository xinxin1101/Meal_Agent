"""Bounded category discovery, deterministic extraction, and raw staging."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .client import MeishiChinaHttpClient
from .models import RawMeishiChinaRecipe
from .parser import discover_next_page, discover_recipe_urls, parse_recipe_page, recipe_identity


BATCH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


@dataclass(frozen=True)
class CrawlResult:
    records: list[RawMeishiChinaRecipe]
    category_pages_fetched: int
    detail_pages_fetched: int
    duplicate_count: int


def crawl_category(
    client: MeishiChinaHttpClient,
    category_url: str,
    limit: int = 10,
    max_pages: int = 1,
    known_source_ids: set[str] | None = None,
) -> CrawlResult:
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if not 1 <= max_pages <= 3:
        raise ValueError("max_pages must be between 1 and 3")
    page_url: str | None = category_url
    unseen_urls: list[str] = []
    known_urls: list[str] = []
    known = known_source_ids or set()
    category_pages_fetched = 0
    while page_url and category_pages_fetched < max_pages and len(unseen_urls) < limit:
        html = client.get_html(page_url)
        category_pages_fetched += 1
        for url in discover_recipe_urls(html, page_url, limit=20):
            source_number, _ = recipe_identity(url)
            destination = known_urls if f"meishichina:{source_number}" in known else unseen_urls
            if url not in unseen_urls and url not in known_urls:
                destination.append(url)
        page_url = discover_next_page(html, page_url)
    recipe_urls = [*unseen_urls[:limit], *known_urls[: max(0, limit - len(unseen_urls))]]
    if not recipe_urls:
        raise ValueError("NO_RECIPE_URLS_DISCOVERED")

    records: list[RawMeishiChinaRecipe] = []
    source_ids: set[str] = set()
    content_hashes: set[str] = set()
    duplicate_count = 0
    detail_pages_fetched = 0
    captured_at = datetime.now(timezone.utc)
    for recipe_url in recipe_urls[:limit]:
        html = client.get_html(recipe_url)
        detail_pages_fetched += 1
        record = parse_recipe_page(html, recipe_url, captured_at=captured_at)
        if record.source_id in source_ids or record.raw_content_hash in content_hashes:
            duplicate_count += 1
            continue
        source_ids.add(record.source_id)
        content_hashes.add(record.raw_content_hash)
        records.append(record)
    return CrawlResult(records, category_pages_fetched, detail_pages_fetched, duplicate_count)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stage_batch(
    records: Iterable[RawMeishiChinaRecipe],
    staging_root: Path,
    batch_id: str,
    category_url: str,
    category_pages_fetched: int,
    detail_pages_fetched: int,
    duplicate_count: int,
) -> dict[str, object]:
    if not BATCH_PATTERN.fullmatch(batch_id):
        raise ValueError("batch_id must match [a-z0-9][a-z0-9-]{2,79}")
    materialized = list(records)
    if not 1 <= len(materialized) <= 20:
        raise ValueError("a staged batch must contain between 1 and 20 records")
    batch_dir = staging_root.resolve() / batch_id
    if batch_dir.exists():
        raise FileExistsError(f"Batch already exists: {batch_dir}")
    batch_dir.mkdir(parents=True)
    with (batch_dir / "records.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for record in materialized:
            handle.write(canonical_json(record.model_dump(mode="json")) + "\n")
    created_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, object] = {
        "schema_version": "mealpilot.raw-meishichina-batch.v1",
        "batch_id": batch_id,
        "created_at": created_at,
        "source_tool": "MealPilot MeishiChinaImporter",
        "source_site": "meishichina.com",
        "category_url": category_url,
        "records_file": "records.jsonl",
        "imported_count": len(materialized),
        "category_pages_fetched": category_pages_fetched,
        "detail_pages_fetched": detail_pages_fetched,
        "duplicate_count": duplicate_count,
        "image_download_count": 0,
        "batch_status": "RAW_QUARANTINED",
        "publication_eligible": False,
        "license_status": "PENDING",
        "review_status": "PENDING",
        "required_next_steps": [
            "source_and_licence_review",
            "ingredient_and_unit_normalisation",
            "image_independence_review",
            "nutrition_allergen_and_time_validation",
            "explicit_acceptance",
        ],
    }
    with (batch_dir / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest
