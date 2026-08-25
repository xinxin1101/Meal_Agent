# Recipe data pipeline

```text
approved importer / bounded acquisition
  -> raw quarantine
  -> source/provenance review
  -> deterministic extraction
  -> ingredient/unit normalization
  -> allergen and cooking-quality gates
  -> LLM display-field proposal
  -> Pydantic + deterministic final validation
  -> administrator batch publication
     -> readable recipe library
     -> menu-draft subset
     -> Solver-eligible subset
```

Fetched content is untrusted data, never instructions. It cannot change prompts, permissions, tools, or publication state. No importer writes directly to `data/recipes.published.json`; raw content keeps source URL/id, capture time, content hash, source terms, original ingredient text, warnings, and pending review state.

## MeishiChina prototype

The MC-R1/R2 importer downloads no images, respects robots/rate limits/retries, hard-stops on access challenges, and stages a bounded batch under `data/staging/raw/<batch_id>`. Offline HTML fixtures make CI independent of the live site.

MC-R3 v2 normalizes explicit mass as Decimal grams and recognizes only reviewed `适量`/`少许` as qualitative quantities. Other ambiguous phrases remain blockers until a reviewer maps them. It never invents ingredient mass, allergens, or nutrition. Ordered steps must be executable without images; medical claims block publication.

The gates create three independent product capabilities:

- `READABLE`: title, source-preserving ingredients, and actionable ordered steps are present. A missing serving count, time, meal slot, nutrition basis, exact mass, or full allergen composition is displayed as a warning rather than silently invented.
- `PUBLICATION_READY` / menu-draft eligible: the readable gate plus reviewed servings, preparation time, and an actual meal slot. It can form an explicitly unverified breakfast/lunch/dinner menu only when all three slots have real candidates.
- `SOLVER_READY`: the stricter menu gate plus complete authoritative nutrition and the quantity/allergen evidence required by deterministic nutrition planning.

`BLOCKED` is therefore not synonymous with “unpublishable”: a `BLOCKED` record that passes the readable gate can be batch-published only to the recipe library. It cannot be used to make a menu or a nutrition claim.

Nutritionally material qualitative ingredients such as oil, sugar, fatty meat, nuts, sesame, and energy-dense sauces block Solver use unless reviewed/source-declared nutrition covers the complete serving. Minor qualitative seasonings may be explicitly marked `EXCLUDED_MINOR_INGREDIENT`; that decision is auditable.

MC-R4 keeps an OCC-versioned audit trail and immutable source provenance. For the explicitly non-commercial personal-study deployment, the active workflow does not collect licence-proof forms; every record still retains source URL, capture time, source terms, and content hash. An administrator must explicitly confirm any batch publication. This does not grant public redistribution rights.

## Runtime boundary

The recipe library reads the configured readable published dataset. The menu draft reads only the stricter published menu dataset, while strict planning reads only the Solver-ready subset. The repository sample corpus is test-only and is loaded only when `MEALPILOT_INCLUDE_SAMPLE_RECIPES=true`; production and normal development default to `false`. No user-facing path reads staging or review storage directly.

Automatic acquisition may run only with bounded batches, deduplication, raw quarantine, LLM schema validation and deterministic final validation. It never auto-publishes; every item stops for administrator review. Readable drafts may be published to the recipe library, `PUBLICATION_READY` and `SOLVER_READY` drafts may additionally enter the menu dataset, and only the latter can enter strict nutrition planning.

Automatic LLM structuring follows [llm_recipe_curation.md](llm_recipe_curation.md). It produces only typed title/step/serving/slot/time/mapping fields. Pydantic validation, the local canonical-food allowlist, deterministic safety/nutrition gates and administrator review remain mandatory.

The ADMIN review workbench exposes the same boundaries through authenticated APIs. Every mutation requires an `Idempotency-Key` and `expected_review_version`. Browser submissions may select only local canonical ingredient IDs; canonical names and allergen facts are supplied by the server. The workbench clearly separates readable, menu, and Solver blockers, and its publication controls never promote a lower capability to a higher one.
