import hashlib
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "import_nutrition_catalog.py"


def _candidate(path: Path) -> str:
    payload = [{
        "canonical_id": "tofu",
        "food_data_id": "trusted-tofu-001",
        "nutrition_per_100g": {
            "energy_kcal": "76",
            "protein_g": "8.1",
            "carbohydrate_g": "1.9",
            "fat_g": "4.8",
        },
        "source": {
            "source_id": "trusted-nutrition-source",
            "source_url": "https://nutrition.example.org/foods/tofu",
            "license": "CC-BY-4.0",
            "data_version": "2026-08",
        },
    }]
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_inspects_then_installs_the_exact_reviewed_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    digest = _candidate(candidate)

    inspect = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(candidate), "--project-root", str(tmp_path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspect.returncode == 0, inspect.stderr
    inspected = json.loads(inspect.stdout)
    assert inspected["installed"] is False
    assert inspected["report"]["content_sha256"] == digest

    missing_hash = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--input", str(candidate), "--project-root", str(tmp_path),
            "--install", "--confirm-reviewed",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_hash.returncode == 2
    assert json.loads(missing_hash.stdout)["reason_code"] == "EXPECTED_SHA256_REQUIRED_FOR_INSTALL"

    install = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--input", str(candidate), "--project-root", str(tmp_path),
            "--expected-sha256", digest, "--install", "--confirm-reviewed",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr
    installed = json.loads(install.stdout)
    destination = tmp_path / ".runtime" / "recipe-data" / "nutrition" / "foods.json"
    assert installed["installed"] is True
    assert Path(installed["destination"]) == destination.resolve()
    assert destination.read_bytes() == candidate.read_bytes()
