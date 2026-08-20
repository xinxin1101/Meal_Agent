from pathlib import Path

from mealpilot.ingestion.settings import load_recipe_data_paths


def test_unified_recipe_root_contains_all_acquisition_artifacts(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "persistent-recipes"
    monkeypatch.setenv("MEALPILOT_RECIPE_DATA_ROOT", str(root))
    paths = load_recipe_data_paths(tmp_path)
    assert paths.raw == root / "raw"
    assert paths.reviews == root / "reviews"
    assert paths.published == root / "published" / "recipes.json"
    assert paths.state == root / "state" / "meishichina.json"
    assert paths.jobs == root / "jobs" / "acquisition.sqlite3"
    assert all(str(value).startswith(str(root)) for value in (paths.raw, paths.reviews, paths.published, paths.state, paths.jobs))
