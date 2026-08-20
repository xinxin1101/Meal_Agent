from pathlib import Path

from mealpilot.evaluation import evaluate_scenarios


def test_version_pinned_evaluation_scenarios_pass() -> None:
    report = evaluate_scenarios(Path("."))
    assert report.nutrition_data_version == "2026-08"
    assert all(case.passed for case in report.cases)
