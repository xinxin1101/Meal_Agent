from pathlib import Path

import mealpilot.main as main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_and_acquisition_do_not_hardwire_sample_nutrition() -> None:
    runtime_source = (PROJECT_ROOT / "backend" / "mealpilot" / "main.py").read_text(encoding="utf-8")
    acquisition_source = (
        PROJECT_ROOT / "backend" / "mealpilot" / "ingestion" / "acquisition.py"
    ).read_text(encoding="utf-8")

    assert "foods.sample.json" not in runtime_source
    assert "foods.sample.json" not in acquisition_source
    assert "MEALPILOT_NUTRITION_DATA_PATH" in runtime_source
    assert "recipe_data_paths.nutrition" in runtime_source
    assert "paths.nutrition" in acquisition_source


def test_missing_formal_nutrition_catalog_fails_closed(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "nutrition" / "foods.json"
    monkeypatch.setenv("MEALPILOT_NUTRITION_DATA_PATH", str(missing))

    assert main._nutrition_catalog() == []
    assert main._catalog_coverage().complete is False


def test_explicit_test_nutrition_catalog_remains_loadable(monkeypatch) -> None:
    fixture = PROJECT_ROOT / "data" / "nutrition" / "foods.sample.json"
    monkeypatch.setenv("MEALPILOT_NUTRITION_DATA_PATH", str(fixture))

    catalog = main._nutrition_catalog()
    assert catalog
    assert {item.source.license for item in catalog} == {"internal-development-only"}
