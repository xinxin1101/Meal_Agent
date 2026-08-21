import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_solver_readiness_cli_reports_sample_fixture_without_mutation(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.update(
        {
            "MEALPILOT_RUNTIME_DIR": str(tmp_path / "runtime"),
            "MEALPILOT_RECIPE_DATA_ROOT": str(tmp_path / "recipe-data"),
            "MEALPILOT_INCLUDE_SAMPLE_RECIPES": "true",
            "MEALPILOT_PUBLISHED_RECIPES_PATH": str(PROJECT_ROOT / "tests" / "fixtures" / "no-runtime-recipes.json"),
            "MEALPILOT_NUTRITION_DATA_PATH": str(PROJECT_ROOT / "data" / "nutrition" / "foods.sample.json"),
            "MEALPILOT_ADMIN_PASSWORD": "",
            "SILICONFLOW_API_KEY": "",
            "SILICONFLOW_MODEL": "",
        }
    )

    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "report_solver_readiness.py"), "--project-root", str(PROJECT_ROOT)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["published_recipe_count"] >= 3
    assert report["solver_ready_count"] >= 3
    assert report["formal_nutrition_record_count"] > 0
    assert report["strict_planning_ready"] is True
    assert report["actions"] == []


def test_solver_readiness_cli_can_fail_a_gate_when_formal_corpus_is_not_ready(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.update(
        {
            "MEALPILOT_RUNTIME_DIR": str(tmp_path / "runtime"),
            "MEALPILOT_RECIPE_DATA_ROOT": str(tmp_path / "recipe-data"),
            "MEALPILOT_INCLUDE_SAMPLE_RECIPES": "false",
            "MEALPILOT_PUBLISHED_RECIPES_PATH": str(PROJECT_ROOT / "tests" / "fixtures" / "no-runtime-recipes.json"),
            "MEALPILOT_NUTRITION_DATA_PATH": str(tmp_path / "missing-foods.json"),
            "MEALPILOT_ADMIN_PASSWORD": "",
        }
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "report_solver_readiness.py"),
            "--project-root",
            str(PROJECT_ROOT),
            "--fail-if-not-ready",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 3, completed.stderr
    report = json.loads(completed.stdout)
    assert report["strict_planning_ready"] is False
    assert report["published_recipe_count"] == 0
    assert report["solver_ready_count"] == 0
