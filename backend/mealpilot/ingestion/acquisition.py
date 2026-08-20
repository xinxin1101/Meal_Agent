"""Reviewed policy registry and execution boundary for recipe acquisition."""

from __future__ import annotations

from pathlib import Path

from mealpilot.ingestion.automation import SourceAutomationPolicy, run_incremental_automation
from mealpilot.ingestion.jobs import RecipeAcquisitionJob, SourcePolicySummary
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.ingestion.settings import RecipeDataPaths
from mealpilot.ingestion.sources.meishichina.client import FetchPolicy, MeishiChinaHttpClient
from mealpilot.nutrition.loaders import load_food_catalog


POLICY_FILES = {"meishichina.personal-study": "meishichina.personal-study.json"}


def load_source_policy(project_root: Path, policy_id: str) -> SourceAutomationPolicy:
    filename = POLICY_FILES.get(policy_id)
    if filename is None:
        raise KeyError(policy_id)
    path = project_root / "config" / "recipe_sources" / filename
    return SourceAutomationPolicy.model_validate_json(path.read_text(encoding="utf-8"))


def list_source_policies(project_root: Path) -> list[SourceAutomationPolicy]:
    return [load_source_policy(project_root, policy_id) for policy_id in sorted(POLICY_FILES)]


def policy_summary(policy: SourceAutomationPolicy) -> SourcePolicySummary:
    return SourcePolicySummary(
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        enabled=policy.enabled,
        purpose=policy.purpose,
        max_records_per_run=policy.max_records_per_run,
        max_pages_per_category=policy.max_pages_per_category,
        minimum_delay_seconds=str(policy.minimum_delay_seconds),
        category_count=len(policy.category_urls),
    )


def execute_acquisition_job(project_root: Path, paths: RecipeDataPaths, job: RecipeAcquisitionJob) -> dict[str, object]:
    """Execute one policy-bound job. Never approves or publishes records."""
    configured = load_source_policy(project_root, job.policy_id)
    if not configured.enabled:
        raise ValueError("AUTOMATION_POLICY_DISABLED")
    policy = configured.model_copy(update={
        "max_records_per_run": min(job.max_records, configured.max_records_per_run),
        "auto_publish": False,
        "auto_publish_source_ids": [],
    })
    review = ReviewService(ReviewStore(paths.reviews), load_food_catalog(project_root / "data" / "nutrition" / "foods.sample.json"))
    fetch = FetchPolicy(delay_seconds=policy.minimum_delay_seconds)
    with MeishiChinaHttpClient(policy=fetch) as client:
        report = run_incremental_automation(
            policy, client, review, paths.raw, paths.state, paths.published,
            actor=f"recipe-worker:{job.job_id}", batch_id=f"{job.job_id}-batch",
        )
    payload = report.model_dump(mode="json")
    # Absolute host paths and source content are operational details, not API output.
    payload.pop("batch_path", None)
    return payload

