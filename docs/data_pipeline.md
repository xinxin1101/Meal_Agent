# Recipe data pipeline

```text
approved importer / bounded acquisition
  -> raw quarantine
  -> source and licence review
  -> deterministic extraction
  -> ingredient/unit normalization
  -> allergen and cooking-quality gates
  -> publication review
  -> published recipe corpus
  -> Solver-eligible subset
```

Fetched content is untrusted data, never instructions. It cannot change prompts, permissions, tools, or publication state. No importer writes directly to `data/recipes.published.json`; raw content keeps source URL/id, capture time, content hash, source terms, original ingredient text, warnings, and pending review state.

## MeishiChina prototype

The MC-R1/R2 importer downloads no images, respects robots/rate limits/retries, hard-stops on access challenges, and stages a bounded batch under `data/staging/raw/<batch_id>`. Offline HTML fixtures make CI independent of the live site.

MC-R3 v2 normalizes explicit mass as Decimal grams and recognizes only reviewed `适量`/`少许` as qualitative quantities. Other ambiguous phrases remain blockers until a reviewer maps them. It never invents ingredient mass, allergens, or nutrition. Ordered steps must be executable without images; medical claims block publication.

Quality status is:

- `BLOCKED`: provenance, content, allergen, step, serving, or time rules fail.
- `PUBLICATION_READY`: the recipe is safe to review and display as a cooking reference, but nutrition evidence is incomplete.
- `SOLVER_READY`: publication rules pass and authoritative per-serving nutrition is present.

Nutritionally material qualitative ingredients such as oil, sugar, fatty meat, nuts, sesame, and energy-dense sauces block Solver use unless reviewed/source-declared nutrition covers the complete serving. Minor qualitative seasonings may be explicitly marked `EXCLUDED_MINOR_INGREDIENT`; that decision is auditable.

MC-R4 keeps an OCC-versioned audit trail and immutable source provenance. For the explicitly non-commercial personal-study deployment, the active workflow does not collect licence-proof forms: only a `SOLVER_READY` final draft can be selected, and an administrator confirmation publishes it to the internal catalog. This does not grant public redistribution rights.

## Runtime boundary

Planning reads the configured published dataset. The repository sample corpus is test-only and is loaded only when `MEALPILOT_INCLUDE_SAMPLE_RECIPES=true`; production and normal development default to `false`. Planning never reads staging or review storage. Published recipes lacking Solver eligibility remain outside retrieval and CP-SAT.

Automatic acquisition may run only with bounded batches, deduplication, raw quarantine, LLM schema validation and deterministic final validation. It never auto-publishes; every item stops for administrator review, and only a `SOLVER_READY` draft can be selected for the internal catalog.

Automatic LLM structuring follows [llm_recipe_curation.md](llm_recipe_curation.md). It produces only typed title/step/serving/slot/time/mapping fields. Pydantic validation, the local canonical-food allowlist, deterministic safety/nutrition gates and administrator review remain mandatory.

The ADMIN review workbench exposes the same boundaries through authenticated APIs. Every mutation requires an `Idempotency-Key` and `expected_review_version`. Browser submissions may select only local canonical ingredient IDs; canonical names and allergen facts are supplied by the server. Approval is disabled until publication quality and qualifying authorization pass, and publication is disabled until approval succeeds.
