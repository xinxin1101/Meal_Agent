# Automatic LLM recipe structuring boundary

The LLM is an automatic, bounded structuring step between deterministic initial validation and deterministic final validation. It is not a validator, nutrition source, permission system, or publisher.

```text
untrusted raw recipe
  -> local source-bound complete draft (ingredient identity, quantity and untouched steps)
  -> bounded SiliconFlow metadata request (no secrets or user health profile)
  -> JSON Schema metadata response (servings, meal slots, time and optional step rewrites)
  -> local merge into RecipeCurationProposal (complete v3 JSON)
  -> exact ingredient/step coverage and source-binding checks
  -> locally assigned ingredient identity and deterministic quantity parsing
  -> local allergen facts and canonical names
  -> build_quality_draft() deterministic revalidation
  -> FINAL_VALIDATED or FINAL_VALIDATION_BLOCKED
  -> administrator selection
  -> approve + publish to the internal personal-study catalog
```

The persisted v3 proposal includes every editable page field and every source row: schema version, display title, servings, supported meal slots, preparation minutes, a complete ordered ingredient array, a complete ordered cooking-step array, and reviewer notes. The model only returns the small `mealpilot.recipe-display-metadata.v1` supplement. Local code fills each ingredient's source index/name, locally assigned canonical identity, source-verified quantity and nutrition role, then merges source steps with any source-bound rewrites. This prevents the model from repeatedly emitting high-volume facts it is not allowed to decide.

The model cannot omit source rows or add new ones. It also cannot provide prices, equipment, nutrition values, allergen facts, medical claims, approval, or publication state. A source ingredient name is a stable identity by default; reviewed aliases may collapse to an existing identity. Unknown allergen composition must not be inferred from the name: it is retained as a display warning and forces `solver_eligible=false`, so planning remains fail-closed. Local code accepts only quantities verifiable from source text and recomputes the complete quality report. The request uses JSON Schema output with thinking disabled for this bounded data-entry task. A failure is exposed as a machine-readable error in the administrator workbench.

Run one proposal explicitly:

```powershell
python scripts/review_meishichina.py assist --review-id <id> --expected-version <version> --actor root
```

`POST /v1/admin/reviews/{review_id}/assist` queues a lease-based background job and returns `202`. The standalone recipe Worker executes the model call, then records provider, model, prompt version, proposal hash, accepted/rejected suggestions, and an `LLM_ASSISTED` event. Provider or schema failure becomes `LLM_FAILED`. A schema-valid proposal pre-fills the administrator workbench; the administrator may correct any value but is not required to manually create the structure. The record remains `PENDING` until the deterministic publication gate passes and an administrator approves it. `PUBLICATION_READY` recipes may be shown as cooking pages, while only `SOLVER_READY` recipes may enter planning.
