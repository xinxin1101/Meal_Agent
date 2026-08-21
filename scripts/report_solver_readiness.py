"""Print an actionable publication/Solver readiness report for the current corpus."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect current recipe publication and Solver-readiness blockers without mutating runtime state.",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--fail-if-not-ready",
        action="store_true",
        help="Return exit code 3 when breakfast/lunch/dinner Solver-ready coverage is incomplete.",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    os.environ["MEALPILOT_PROJECT_ROOT"] = str(project_root)

    # Import only after the project-root override is applied because main owns the
    # runtime recipe/review paths used by the administrator catalog projection.
    import mealpilot.main as runtime_main  # noqa: E402
    from mealpilot.nutrition.readiness import build_solver_readiness_report  # noqa: E402

    report = build_solver_readiness_report(
        runtime_main.admin_recipes(),
        runtime_main._nutrition_catalog(),
    )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    if args.fail_if_not_ready and not report.strict_planning_ready:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
