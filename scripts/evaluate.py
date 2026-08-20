"""Generate a reproducible local-MVP evaluation report."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from mealpilot.evaluation import evaluate_scenarios  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".runtime" / "evaluation-report.json")
    args = parser.parse_args()
    report = evaluate_scenarios(PROJECT_ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [item.scenario_id for item in report.cases if not item.passed]
    print(json.dumps({"passed": len(report.cases) - len(failures), "total": len(report.cases), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
