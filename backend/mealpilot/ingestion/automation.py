"""Bounded incremental acquisition and review orchestration.

This module never authenticates to a source or bypasses access controls. New
records are initially validated, sent through bounded LLM structuring, then
deterministically revalidated. Publication always remains an administrator act.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from mealpilot.ingestion.review import ReviewConflict, ReviewRejected, ReviewService
from mealpilot.ingestion.sources.meishichina.client import MeishiChinaHttpClient
from mealpilot.ingestion.sources.meishichina.crawler import crawl_category, stage_batch
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe


class SourceAutomationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    policy_version: str
    enabled: bool = False
    purpose: Literal["PERSONAL_STUDY"] = "PERSONAL_STUDY"
    category_urls: list[HttpUrl] = Field(min_length=1, max_length=5)
    max_records_per_run: int = Field(default=10, ge=1, le=20)
    max_pages_per_category: int = Field(default=1, ge=1, le=3)
    minimum_delay_seconds: float = Field(default=2.0, ge=2.0)
    auto_publish: bool = False
    auto_publish_source_ids: list[str] = Field(default_factory=list, max_length=100)
    dataset_version: str = "contract-v2-auto-v1"

    @field_validator("category_urls")
    @classmethod
    def same_public_source_only(cls, values: list[HttpUrl]) -> list[HttpUrl]:
        for value in values:
            parsed = urlparse(str(value))
            if parsed.scheme != "https" or parsed.hostname != "home.meishichina.com":
                raise ValueError("automation category URL must use home.meishichina.com over HTTPS")
        return values


class AutomationState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["mealpilot.recipe-automation-state.v1"] = "mealpilot.recipe-automation-state.v1"
    source_hashes: dict[str, str] = Field(default_factory=dict)
    last_success_at: datetime | None = None


class AutomationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    started_at: datetime
    completed_at: datetime
    discovered_count: int
    new_or_changed_count: int
    unchanged_count: int
    duplicate_count: int
    batch_path: str | None
    reviews_created: list[str]
    reviews_existing: list[str]
    quality_counts: dict[str, int]
    processing_counts: dict[str, int]
    auto_published_recipe_ids: list[str]
    publication_stops: dict[str, list[str]]


def load_automation_state(path: Path) -> AutomationState:
    if not path.exists():
        return AutomationState()
    return AutomationState.model_validate_json(path.read_text(encoding="utf-8"))


def save_automation_state(path: Path, state: AutomationState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def run_incremental_automation(
    policy: SourceAutomationPolicy,
    client: MeishiChinaHttpClient,
    review: ReviewService,
    staging_root: Path,
    state_path: Path,
    published_path: Path,
    *,
    actor: str = "recipe-automation",
    batch_id: str | None = None,
) -> AutomationReport:
    if not policy.enabled:
        raise ValueError("AUTOMATION_POLICY_DISABLED")
    if client.policy.delay_seconds < policy.minimum_delay_seconds:
        raise ValueError("CLIENT_RATE_LIMIT_IS_WEAKER_THAN_SOURCE_POLICY")
    started = datetime.now(timezone.utc)
    state = load_automation_state(state_path)
    records: list[RawMeishiChinaRecipe] = []
    seen_source_ids: set[str] = set()
    duplicates = 0
    category_pages = 0
    detail_pages = 0
    for category_url in policy.category_urls:
        remaining = policy.max_records_per_run - len(records)
        if remaining <= 0:
            break
        result = crawl_category(
            client,
            str(category_url),
            limit=remaining,
            max_pages=policy.max_pages_per_category,
            known_source_ids={*state.source_hashes, *seen_source_ids},
        )
        category_pages += result.category_pages_fetched
        detail_pages += result.detail_pages_fetched
        duplicates += result.duplicate_count
        for item in result.records:
            if item.source_id in seen_source_ids:
                duplicates += 1
                continue
            seen_source_ids.add(item.source_id)
            records.append(item)

    changed = [item for item in records if state.source_hashes.get(item.source_id) != item.raw_content_hash]
    unchanged = len(records) - len(changed)
    created: list[str] = []
    existing: list[str] = []
    quality_counts = {"BLOCKED": 0, "PUBLICATION_READY": 0, "SOLVER_READY": 0}
    processing_counts = {"INITIAL_VALIDATED": 0, "LLM_FAILED": 0, "FINAL_VALIDATION_BLOCKED": 0, "FINAL_VALIDATED": 0}
    auto_published: list[str] = []
    stops: dict[str, list[str]] = {}
    batch_path: str | None = None

    if changed:
        resolved_batch_id = batch_id or f"meishichina-auto-{started:%Y%m%d-%H%M%S}"
        manifest = stage_batch(changed, staging_root, resolved_batch_id, ",".join(str(value) for value in policy.category_urls), category_pages, detail_pages, duplicates)
        batch_path = str(staging_root.resolve() / str(manifest["batch_id"]))
        for raw in changed:
            review_id = f"review-mc-{raw.source_id.rsplit(':', 1)[1]}-{raw.raw_content_hash[:8]}"
            try:
                item = review.prepare(raw, actor=actor)
                created.append(item.review_id)
            except ReviewConflict as error:
                if str(error) != "REVIEW_ALREADY_EXISTS":
                    raise
                existing.append(review_id)
                item = review.store.get(review_id)
            quality_counts[item.quality_report.status] += 1
            processing_counts[item.processing_stage] += 1
            stops[review_id] = ["ADMIN_BATCH_REVIEW_REQUIRED"]

        next_hashes = dict(state.source_hashes)
        next_hashes.update({item.source_id: item.raw_content_hash for item in changed})
        save_automation_state(state_path, AutomationState(source_hashes=next_hashes, last_success_at=datetime.now(timezone.utc)))

    return AutomationReport(
        policy_id=policy.policy_id,
        started_at=started,
        completed_at=datetime.now(timezone.utc),
        discovered_count=len(records),
        new_or_changed_count=len(changed),
        unchanged_count=unchanged,
        duplicate_count=duplicates,
        batch_path=batch_path,
        reviews_created=created,
        reviews_existing=existing,
        quality_counts=quality_counts,
        processing_counts=processing_counts,
        auto_published_recipe_ids=auto_published,
        publication_stops=stops,
    )
