"""Inspect or install one deterministic Solver-ready recipe promotion."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from mealpilot.ingestion.settings import load_recipe_data_paths  # noqa: E402
from mealpilot.nutrition.loaders import load_food_catalog  # noqa: E402
from mealpilot.nutrition.promotion import (  # noqa: E402
    SolverPromotionRejected,
    inspect_catalog_promotion,
    promote_published_recipe,
    sha256_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically revalidate one published display recipe and optionally promote it to Solver-ready.",
    )
    parser.add_argument("--recipe-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--published-path", type=Path)
    parser.add_argument("--nutrition-path", type=Path)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--confirm-reviewed", action="store_true")
    parser.add_argument("--expected-current-catalog-sha256")
    parser.add_argument("--expected-candidate-sha256")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    paths = load_recipe_data_paths(project_root)
    published_path = (args.published_path or paths.published).resolve()
    nutrition_path = (args.nutrition_path or paths.nutrition).resolve()

    if not nutrition_path.is_file():
        print(json.dumps({"ok": False, "reason_code": "FORMAL_NUTRITION_CATALOG_NOT_FOUND"}, ensure_ascii=False))
        return 2

    foods = load_food_catalog(nutrition_path)
    try:
        if args.install:
            if not args.expected_current_catalog_sha256:
                raise SolverPromotionRejected("EXPECTED_CURRENT_CATALOG_SHA256_REQUIRED")
            if not args.expected_candidate_sha256:
                raise SolverPromotionRejected("EXPECTED_CANDIDATE_SHA256_REQUIRED")
            receipt = promote_published_recipe(
                published_path,
                foods,
                recipe_id=args.recipe_id,
                version=args.version,
                expected_current_catalog_sha256=args.expected_current_catalog_sha256,
                expected_candidate_sha256=args.expected_candidate_sha256,
                confirm_reviewed=args.confirm_reviewed,
            )
            print(json.dumps({"ok": True, "installed": True, "receipt": receipt.model_dump(mode="json")}, ensure_ascii=False, indent=2))
            return 0

        candidate = inspect_catalog_promotion(
            published_path,
            foods,
            recipe_id=args.recipe_id,
            version=args.version,
        )
        print(json.dumps(
            {
                "ok": True,
                "installed": False,
                "current_catalog_sha256": sha256_file(published_path),
                "candidate": candidate.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    except SolverPromotionRejected as error:
        print(json.dumps({"ok": False, "reason_code": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
