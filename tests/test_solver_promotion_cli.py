import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _recipe() -> dict[str, object]:
    return {
        "recipe_id": "recipe-meishichina-101-v1",
        "version": "mc-source-v1",
        "title": "可信测试菜谱",
        "supported_slots": ["breakfast"],
        "servings": "1",
        "prep_minutes": 10,
        "nutrition_per_serving": None,
        "nutrition_basis": None,
        "solver_eligible": False,
        "ingredients": [
            {
                "canonical_id": "egg",
                "canonical_name": "鸡蛋",
                "display_quantity": "100g",
                "quantity_kind": "MEASURED",
                "amount_g": "100",
                "nutrition_calculation_role": "INCLUDED",
                "allergens": ["egg"],
                "allergen_composition_known": True,
            }
        ],
        "cooking_steps": [{"step_number": 1, "instruction": "将鸡蛋煮熟。", "ingredient_refs": []}],
        "source": {
            "source_id": "meishichina:101",
            "source_url": "https://home.meishichina.com/recipe-101.html",
            "license": "internal-personal-study",
            "data_version": "source-v1",
        },
        "numeric_policy_version": "mc-r3-v3",
        "nutrition_data_version": None,
    }


def _food() -> dict[str, object]:
    return {
        "canonical_id": "egg",
        "food_data_id": "trusted-egg-001",
        "nutrition_per_100g": {
            "energy_kcal": "143",
            "protein_g": "12.6",
            "carbohydrate_g": "0.7",
            "fat_g": "9.5",
        },
        "source": {
            "source_id": "trusted-food-source",
            "source_url": "https://example.org/nutrition/egg",
            "license": "CC-BY-4.0",
            "data_version": "2026-08",
        },
    }


def test_cli_inspects_then_installs_exact_reviewed_promotion(tmp_path: Path) -> None:
    published = tmp_path / "recipes.json"
    nutrition = tmp_path / "foods.json"
    published.write_text(json.dumps([_recipe()], ensure_ascii=False, indent=2), encoding="utf-8")
    nutrition.write_text(json.dumps([_food()], ensure_ascii=False, indent=2), encoding="utf-8")
    env = dict(os.environ)
    env["MEALPILOT_ADMIN_PASSWORD"] = ""

    inspect = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "promote_solver_recipe.py"),
            "--recipe-id", "recipe-meishichina-101-v1",
            "--version", "mc-source-v1",
            "--published-path", str(published),
            "--nutrition-path", str(nutrition),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert inspect.returncode == 0, inspect.stderr
    inspected = json.loads(inspect.stdout)
    assert inspected["installed"] is False
    assert inspected["candidate"]["recipe"]["solver_eligible"] is True

    install = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "promote_solver_recipe.py"),
            "--recipe-id", "recipe-meishichina-101-v1",
            "--version", "mc-source-v1",
            "--published-path", str(published),
            "--nutrition-path", str(nutrition),
            "--install",
            "--confirm-reviewed",
            "--expected-current-catalog-sha256", inspected["current_catalog_sha256"],
            "--expected-candidate-sha256", inspected["candidate"]["proposed_recipe_sha256"],
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr
    installed = json.loads(install.stdout)
    assert installed["installed"] is True
    payload = json.loads(published.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["solver_eligible"] is True
    assert payload[0]["nutrition_data_version"] == "2026-08"
    assert payload[0]["version"] == installed["receipt"]["promoted_version"]


def test_cli_refuses_to_promote_unresolved_quantities(tmp_path: Path) -> None:
    published = tmp_path / "recipes.json"
    nutrition = tmp_path / "foods.json"
    recipe = _recipe()
    ingredient = recipe["ingredients"][0]  # type: ignore[index]
    ingredient["display_quantity"] = "适量"  # type: ignore[index]
    ingredient["quantity_kind"] = "QUALITATIVE"  # type: ignore[index]
    ingredient["amount_g"] = None  # type: ignore[index]
    published.write_text(json.dumps([recipe], ensure_ascii=False, indent=2), encoding="utf-8")
    nutrition.write_text(json.dumps([_food()], ensure_ascii=False, indent=2), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "promote_solver_recipe.py"),
            "--recipe-id", "recipe-meishichina-101-v1",
            "--version", "mc-source-v1",
            "--published-path", str(published),
            "--nutrition-path", str(nutrition),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason_code"] == "NUTRITION_QUANTITY_INCOMPLETE"
