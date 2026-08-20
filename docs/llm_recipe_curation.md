# Automatic LLM recipe structuring boundary

The LLM is an automatic, bounded structuring step between deterministic initial validation and deterministic final validation. It is not a validator, nutrition source, permission system, or publisher.

```text
untrusted raw recipe
  -> bounded prompt (no secrets or user health profile)
  -> SiliconFlow complete editable recipe JSON (v3)
  -> RecipeCurationProposal (Pydantic, extra fields forbidden)
  -> exact ingredient/step coverage and source-binding checks
  -> one bounded repair retry when the contract is invalid
  -> locally assigned ingredient identity and deterministic quantity parsing
  -> local allergen facts and canonical names
  -> build_quality_draft() deterministic revalidation
  -> FINAL_VALIDATED or FINAL_VALIDATION_BLOCKED
  -> administrator selection
  -> approve + publish to the internal personal-study catalog
```

The v3 proposal must include every editable page field and every source row: schema version, display title, servings, supported meal slots, preparation minutes, a complete ordered ingredient array, a complete ordered cooking-step array, and reviewer notes. Each ingredient row includes its source index/name, the canonical identity already assigned by local code, source quantity fields, and its nutrition calculation role. The model must preserve that identity; it no longer performs ingredient-name mapping. Legacy patch arrays remain present as empty arrays so the JSON shape is explicit and old audit records remain readable.

The model cannot omit source rows or add new ones. It also cannot provide prices, equipment, nutrition values, allergen facts, medical claims, approval, or publication state. A source ingredient name is a stable identity by default; reviewed aliases may collapse to an existing identity. Unknown allergen composition must not be inferred from the name: it is retained as a display warning and forces `solver_eligible=false`, so planning remains fail-closed. Local code accepts only quantities verifiable from source text and recomputes the complete quality report. When the first response is invalid, the provider receives the same exact JSON template and a stable rejection code for one repair attempt. A second failure is exposed as a machine-readable error in the administrator workbench.

Run one proposal explicitly:

```powershell
python scripts/review_meishichina.py assist --review-id <id> --expected-version <version> --actor root
```

Every successful call records provider, model, prompt version, proposal hash, accepted/rejected suggestions, and an `LLM_ASSISTED` event. Provider or schema failure becomes `LLM_FAILED`. A schema-valid proposal pre-fills the administrator workbench; the administrator may correct any value but is not required to manually create the structure. The record remains `PENDING` until the deterministic publication gate passes and an administrator approves it. `PUBLICATION_READY` recipes may be shown as cooking pages, while only `SOLVER_READY` recipes may enter planning.
