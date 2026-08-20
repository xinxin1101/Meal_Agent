"""Inspect or install a reviewed formal nutrition catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from mealpilot.ingestion.settings import load_recipe_data_paths  # noqa: E402
from mealpilot.nutrition.registry import (  # noqa: E402
    FormalNutritionCatalogRejected,
    inspect_formal_catalog,
    install_formal_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a nutrition candidate and optionally install the exact reviewed bytes into the formal runtime catalog.",
    )
    parser.add_argument("--input", type=Path, required=True, help="Candidate JSON file containing FoodNutrition records.")
    parser.add_argument("--expected-sha256", help="SHA-256 of the reviewed candidate; required with --install.")
    parser.add_argument("--expected-current-sha256", help="Required when replacing an existing formal catalog.")
    parser.add_argument("--install", action="store_true", help="Atomically install the candidate into the formal runtime path.")
    parser.add_argument("--confirm-reviewed", action="store_true", help="Explicitly confirm the candidate was reviewed before installation.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    candidate = args.input.resolve()
    destination = load_recipe_data_paths(project_root).nutrition

    try:
        if args.install:
            if not args.expected_sha256:
                raise FormalNutritionCatalogRejected("EXPECTED_SHA256_REQUIRED_FOR_INSTALL")
            report = install_formal_catalog(
                candidate,
                destination,
                expected_candidate_sha256=args.expected_sha256,
                confirm_reviewed=args.confirm_reviewed,
                expected_current_sha256=args.expected_current_sha256,
            )
        else:
            _, report = inspect_formal_catalog(candidate, expected_sha256=args.expected_sha256)
    except FormalNutritionCatalogRejected as error:
        print(json.dumps({"ok": False, "reason_code": str(error)}, ensure_ascii=False))
        return 2

    print(json.dumps({
        "ok": True,
        "installed": args.install,
        "destination": str(destination),
        "report": report.model_dump(mode="json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
