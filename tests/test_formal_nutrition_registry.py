import json
from pathlib import Path

import pytest

from mealpilot.nutrition.registry import (
    FormalNutritionCatalogRejected,
    inspect_formal_catalog,
    install_formal_catalog,
    sha256_file,
)


def _record(
    canonical_id: str = "egg",
    food_data_id: str = "trusted-egg-001",
    *,
    license_name: str = "CC-BY-4.0",
    source_url: str = "https://nutrition.example.org/foods/egg",
) -> dict[str, object]:
    return {
        "canonical_id": canonical_id,
        "food_data_id": food_data_id,
        "nutrition_per_100g": {
            "energy_kcal": "143",
            "protein_g": "12.6",
            "carbohydrate_g": "0.7",
            "fat_g": "9.5",
        },
        "source": {
            "source_id": "trusted-nutrition-source",
            "source_url": source_url,
            "license": license_name,
            "data_version": "2026-08",
        },
    }


def _write(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def test_inspect_formal_catalog_returns_a_hash_bound_report(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    _write(candidate, [_record()])

    _, report = inspect_formal_catalog(candidate, expected_sha256=sha256_file(candidate))

    assert report.record_count == 1
    assert report.canonical_ids == ["egg"]
    assert report.source_ids == ["trusted-nutrition-source"]
    assert report.data_versions == ["2026-08"]
    assert report.content_sha256 == sha256_file(candidate)


def test_formal_catalog_rejects_test_only_evidence_and_duplicates(tmp_path: Path) -> None:
    test_only = tmp_path / "test-only.json"
    _write(test_only, [_record(license_name="internal-development-only")])
    with pytest.raises(FormalNutritionCatalogRejected, match="TEST_ONLY_LICENSE_NOT_FORMAL"):
        inspect_formal_catalog(test_only)

    invalid_url = tmp_path / "invalid-url.json"
    _write(invalid_url, [_record(source_url="https://nutrition.example.invalid/foods/egg")])
    with pytest.raises(FormalNutritionCatalogRejected, match="TEST_ONLY_SOURCE_URL_NOT_FORMAL"):
        inspect_formal_catalog(invalid_url)

    duplicate = tmp_path / "duplicate.json"
    _write(duplicate, [_record(), _record(food_data_id="trusted-egg-002")])
    with pytest.raises(FormalNutritionCatalogRejected, match="DUPLICATE_CANONICAL_ID"):
        inspect_formal_catalog(duplicate)


def test_install_requires_review_confirmation_and_exact_candidate_hash(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    destination = tmp_path / "formal" / "foods.json"
    _write(candidate, [_record()])
    digest = sha256_file(candidate)

    with pytest.raises(FormalNutritionCatalogRejected, match="REVIEW_CONFIRMATION_REQUIRED"):
        install_formal_catalog(
            candidate,
            destination,
            expected_candidate_sha256=digest,
            confirm_reviewed=False,
        )

    with pytest.raises(FormalNutritionCatalogRejected, match="CANDIDATE_SHA256_MISMATCH"):
        install_formal_catalog(
            candidate,
            destination,
            expected_candidate_sha256="0" * 64,
            confirm_reviewed=True,
        )

    report = install_formal_catalog(
        candidate,
        destination,
        expected_candidate_sha256=digest,
        confirm_reviewed=True,
    )
    assert destination.read_bytes() == candidate.read_bytes()
    assert report.content_sha256 == digest


def test_replacement_requires_optimistic_concurrency_hash(tmp_path: Path) -> None:
    destination = tmp_path / "formal" / "foods.json"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write(first, [_record()])
    _write(second, [_record("chicken-breast", "trusted-chicken-001")])

    install_formal_catalog(
        first,
        destination,
        expected_candidate_sha256=sha256_file(first),
        confirm_reviewed=True,
    )
    current_digest = sha256_file(destination)

    with pytest.raises(FormalNutritionCatalogRejected, match="CURRENT_SHA256_REQUIRED_FOR_REPLACEMENT"):
        install_formal_catalog(
            second,
            destination,
            expected_candidate_sha256=sha256_file(second),
            confirm_reviewed=True,
        )

    with pytest.raises(FormalNutritionCatalogRejected, match="CURRENT_SHA256_MISMATCH"):
        install_formal_catalog(
            second,
            destination,
            expected_candidate_sha256=sha256_file(second),
            confirm_reviewed=True,
            expected_current_sha256="0" * 64,
        )

    report = install_formal_catalog(
        second,
        destination,
        expected_candidate_sha256=sha256_file(second),
        confirm_reviewed=True,
        expected_current_sha256=current_digest,
    )
    assert report.canonical_ids == ["chicken-breast"]
    assert destination.read_bytes() == second.read_bytes()
