import hashlib
import sys
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mealpilot.domain.models import CookingStep
from mealpilot.ingestion.llm_assist import (
    IngredientMappingSuggestion,
    LlmAssistanceRecord,
    PROPOSAL_SCHEMA_VERSION,
    RecipeCurationProposal,
    StructuredIngredientSuggestion,
    StructuredStepSuggestion,
    StepRewriteSuggestion,
    _prompt_messages,
    _validate_complete_proposal,
    apply_proposal,
    request_curation_proposal,
)
from mealpilot.ingestion.quality import LEXICON, normalize_name
from mealpilot.ingestion.sources.meishichina.models import RawMeishiChinaRecipe, RawRecipeIngredient
from mealpilot.ingestion.review import ReviewService, ReviewStore
from mealpilot.nutrition.loaders import load_food_catalog
from tests.test_meishichina_quality_review import _raw_recipe


def raw_recipe(raw_name: str) -> RawMeishiChinaRecipe:
    digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()
    return RawMeishiChinaRecipe(
        staging_id=f"raw-mc-{digest[:24]}", captured_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        source_id="meishichina:990099", source_url="https://home.meishichina.com/recipe-990099.html",
        raw_content_hash=digest, title="LLM boundary fixture",
        ingredients=[RawRecipeIngredient(group="main", raw_name=raw_name, raw_amount="100g", raw_text=f"{raw_name} 100g")],
        cooking_steps=[CookingStep(step_number=1, instruction="清洗后煮熟")],
    )


def record(proposal: RecipeCurationProposal) -> LlmAssistanceRecord:
    return LlmAssistanceRecord(
        model="test-model", generated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        proposal_hash="0" * 64, proposal=proposal,
    )


def test_llm_payload_forbids_safety_and_publication_fields() -> None:
    with pytest.raises(ValidationError):
        RecipeCurationProposal.model_validate({"servings": 2, "allergens": [], "publication_eligible": True})


def test_proposal_uses_only_local_canonical_facts_and_rejects_unknown_ids() -> None:
    raw_name, entry = next(iter(LEXICON.items()))
    raw = raw_recipe(raw_name)
    proposal = RecipeCurationProposal(
        display_title="结构化展示标题", servings=2, supported_slots=["lunch"], prep_minutes=20,
        ingredient_mappings=[
            IngredientMappingSuggestion(raw_name=raw_name, canonical_id=entry.canonical_id),
            IngredientMappingSuggestion(raw_name="not-in-source", canonical_id="untrusted-id"),
        ],
        step_rewrites=[StepRewriteSuggestion(step_number=1, instruction="清洗后煮熟并盛出。")],
    )
    curation, audit = apply_proposal(raw, record(proposal))
    assert curation.ingredient_overrides[0].canonical_id == entry.canonical_id
    assert curation.ingredient_overrides[0].allergens == list(entry.allergens)
    assert any(value.startswith("RAW_NAME_NOT_FOUND") for value in audit.rejected_suggestions)
    assert normalize_name(curation.ingredient_overrides[0].raw_name) == normalize_name(raw_name)
    assert audit.requires_human_review is True
    assert audit.deterministic_revalidation_required is True
    assert curation.title_override == "结构化展示标题"
    assert curation.step_overrides == {1: "清洗后煮熟并盛出。"}


def test_llm_structure_is_followed_by_deterministic_final_validation(tmp_path: Path, monkeypatch) -> None:
    raw = _raw_recipe()
    proposal = RecipeCurationProposal(
        display_title="牛肉西兰花",
        servings=2,
        supported_slots=["lunch", "dinner"],
        prep_minutes=20,
        step_rewrites=[],
    )
    monkeypatch.setattr("mealpilot.ingestion.review.request_curation_proposal", lambda _raw: record(proposal))
    service = ReviewService(ReviewStore(tmp_path / "reviews"), load_food_catalog(Path("data/nutrition/foods.sample.json")))
    item = service.prepare(raw)
    assert item.processing_stage == "INITIAL_VALIDATED"
    item = service.assist(item.review_id, item.review_version, "recipe-worker")
    assert item.processing_stage == "FINAL_VALIDATED"
    assert item.quality_report.status == "SOLVER_READY"
    assert item.draft.title == "牛肉西兰花"


def complete_proposal(raw: RawMeishiChinaRecipe) -> RecipeCurationProposal:
    raw_name, entry = next(iter(LEXICON.items()))
    assert normalize_name(raw.ingredients[0].raw_name) == normalize_name(raw_name)
    return RecipeCurationProposal(
        schema_version=PROPOSAL_SCHEMA_VERSION,
        display_title="Complete display title",
        servings="2",
        supported_slots=["lunch"],
        prep_minutes=20,
        ingredients=[
            StructuredIngredientSuggestion(
                source_index=0,
                raw_name=raw_name,
                canonical_id=entry.canonical_id,
                amount="100",
                unit="g",
            )
        ],
        steps=[StructuredStepSuggestion(step_number=1, instruction=raw.cooking_steps[0].instruction)],
        review_notes=[],
    )


def test_v3_prompt_contains_every_editable_json_field_and_source_row() -> None:
    raw = raw_recipe(next(iter(LEXICON)))
    messages = _prompt_messages(raw)
    assert "expected_response_template" in messages[1]["content"]
    for field in (
        "schema_version", "display_title", "servings", "supported_slots", "prep_minutes",
        "ingredients", "steps", "review_notes", "ingredient_mappings", "step_rewrites",
    ):
        assert f'"{field}"' in messages[1]["content"]


def test_v3_proposal_requires_exact_ingredient_and_step_coverage() -> None:
    raw = raw_recipe(next(iter(LEXICON)))
    proposal = complete_proposal(raw)
    _validate_complete_proposal(raw, proposal)
    with pytest.raises(ValueError, match="LLM_STEP_COVERAGE_MISMATCH"):
        _validate_complete_proposal(raw, proposal.model_copy(update={"steps": []}))


def test_request_retries_once_after_invalid_json(monkeypatch) -> None:
    raw = raw_recipe(next(iter(LEXICON)))
    valid = complete_proposal(raw).model_dump_json(exclude_none=False)
    contents = iter(['{"display_title":"incomplete"}', valid])
    calls: list[object] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(contents)))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **_kwargs: fake_client))
    monkeypatch.setattr(
        "mealpilot.ingestion.llm_assist.load_siliconflow_settings",
        lambda: SimpleNamespace(
            configured=True,
            api_key="test",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
    )
    result = request_curation_proposal(raw)
    assert result.proposal.schema_version == PROPOSAL_SCHEMA_VERSION
    assert len(calls) == 2
    assert "previous_response_rejected_for" in calls[1]["messages"][1]["content"]
