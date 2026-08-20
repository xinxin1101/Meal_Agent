"""Local MC-R3/MC-R4 review CLI; no review endpoint is exposed to ordinary users."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mealpilot.ingestion.quality import RecipeCuration
from mealpilot.ingestion.review import (
    ReviewConflict,
    ReviewRejected,
    ReviewService,
    ReviewStore,
    load_raw_batch,
)
from mealpilot.nutrition.loaders import load_food_catalog
from mealpilot.ingestion.settings import load_recipe_data_paths


ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATHS = load_recipe_data_paths(ROOT)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--review-root", type=Path, default=RECIPE_PATHS.reviews)
    value.add_argument("--foods", type=Path, default=ROOT / "data" / "nutrition" / "foods.sample.json")
    subcommands = value.add_subparsers(dest="command", required=True)

    prepare = subcommands.add_parser("prepare", help="create pending review items from raw records.jsonl")
    prepare.add_argument("--records", type=Path, required=True)
    prepare.add_argument("--actor", default="local-reviewer")

    subcommands.add_parser("list", help="list review summaries")

    show = subcommands.add_parser("show", help="show raw and structured values together")
    show.add_argument("--review-id", required=True)

    template = subcommands.add_parser("template", help="print a curation template")
    template.add_argument("--review-id", required=True)

    curate = subcommands.add_parser("curate", help="apply a completed curation JSON file")
    _mutation_args(curate)
    curate.add_argument("--curation", type=Path, required=True)

    assist = subcommands.add_parser("assist", help="request a bounded LLM proposal and rerun deterministic gates")
    _mutation_args(assist)

    approve = subcommands.add_parser("approve", help="approve only LLM-processed, final SOLVER_READY records")
    _mutation_args(approve)

    reject = subcommands.add_parser("reject", help="reject a pending review")
    _mutation_args(reject)
    reject.add_argument("--reason", required=True)

    publish_parser = subcommands.add_parser("publish", help="publish one approved review through ingestion.publish")
    _mutation_args(publish_parser)
    publish_parser.add_argument("--published-path", type=Path, default=RECIPE_PATHS.published)
    publish_parser.add_argument("--dataset-version", required=True)

    revoke = subcommands.add_parser("revoke", help="revoke approval and withdraw a published version")
    _mutation_args(revoke)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--published-path", type=Path, default=RECIPE_PATHS.published)
    revoke.add_argument("--dataset-version", default="local")
    return value


def _mutation_args(value: argparse.ArgumentParser) -> None:
    value.add_argument("--review-id", required=True)
    value.add_argument("--expected-version", type=int, required=True)
    value.add_argument("--actor", default="local-reviewer")


def service(args: argparse.Namespace) -> ReviewService:
    return ReviewService(
        ReviewStore(args.review_root),
        load_food_catalog(args.foods),
    )


def output(value: object) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    print(json.dumps(value, ensure_ascii=False, indent=2))


def curation_template(item: object) -> dict[str, object]:
    raw = item.raw  # type: ignore[attr-defined]
    return {
        "servings": None,
        "supported_slots": [],
        "prep_minutes": None,
        "nutrition_per_serving_override": None,
        "nutrition_basis": None,
        "nutrition_data_version": None,
        "ingredient_overrides": [
            {
                "raw_name": ingredient.raw_name,
                "canonical_id": "",
                "canonical_name": "",
                "amount": None,
                "unit": None,
                "qualitative_label": ingredient.raw_amount if ingredient.raw_amount in {"适量", "少许"} else None,
                "nutrition_calculation_role": "INCLUDED",
                "allergens": [],
                "allergen_composition_known": False,
                "density_g_per_ml": None,
                "portion_g": None,
            }
            for ingredient in raw.ingredients
        ],
        "step_overrides": {},
        "excluded_step_numbers": [],
    }


def main() -> int:
    args = parser().parse_args()
    review = service(args)
    try:
        if args.command == "prepare":
            created = 0
            skipped = 0
            for raw in load_raw_batch(args.records):
                try:
                    review.prepare(raw, actor=args.actor)
                    created += 1
                except ReviewConflict as error:
                    if str(error) != "REVIEW_ALREADY_EXISTS":
                        raise
                    skipped += 1
            output({"created": created, "skipped_existing": skipped, "review_root": str(args.review_root.resolve())})
        elif args.command == "list":
            output([item.model_dump(mode="json") for item in review.summaries()])
        elif args.command == "show":
            output(review.store.get(args.review_id))
        elif args.command == "template":
            output(curation_template(review.store.get(args.review_id)))
        elif args.command == "curate":
            curation = RecipeCuration.model_validate_json(args.curation.read_text(encoding="utf-8"))
            output(review.curate(args.review_id, curation, args.expected_version, args.actor))
        elif args.command == "assist":
            output(review.assist(args.review_id, args.expected_version, args.actor))
        elif args.command == "approve":
            output(review.approve(args.review_id, args.expected_version, args.actor))
        elif args.command == "reject":
            output(review.reject(args.review_id, args.expected_version, args.actor, args.reason))
        elif args.command == "publish":
            output(review.publish(args.review_id, args.expected_version, args.actor, args.published_path, args.dataset_version))
        elif args.command == "revoke":
            output(review.revoke(args.review_id, args.expected_version, args.actor, args.reason, args.published_path, args.dataset_version))
        return 0
    except (ReviewConflict, ReviewRejected, KeyError, ValueError) as error:
        output({"error": type(error).__name__, "reason": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
