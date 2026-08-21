# M40-F — Real Corpus Funnel Validation

M40-F turns recipe-quality optimization into a measured, repeatable process.
It does **not** mutate review records. It replays the current deterministic
quality gate over the persisted `raw + curation` pair for every review and
reports what the corpus would look like under the current code.

## Why recompute instead of reading stored reports?

Review records can outlive a quality-gate version. A record persisted under
`mc-r3-v2` or `mc-r3-v3` may still say `BLOCKED` even when `mc-r3-v4` would now
classify it as `PUBLICATION_READY`. M40-F therefore treats stored reports only
as historical evidence and calls `build_quality_draft()` again for every
record.

The report contains:

- `BLOCKED / PUBLICATION_READY / SOLVER_READY` counts under the current gate;
- publishable and Solver-ready conversion rates;
- publication blocker frequency and affected review IDs;
- Solver-only blocker frequency;
- deduplicated priority categories such as servings, time, meal slots, steps,
  ingredient quantity, allergen composition, and nutrition coverage;
- stored-gate/version drift compared with the current recomputation;
- per-review evidence showing the exact recomputed blocker lists.

Priority counts are deduplicated by review ID. One recipe with two related
error codes therefore counts as one affected recipe for that category.

## Run against the real local corpus

Real crawl and review data intentionally lives under `.runtime/recipe-data`
and is git-ignored. From the repository root:

```bash
python scripts/audit_recipe_funnel.py \
  --output .runtime/reports/recipe-funnel.json \
  --fail-on-empty
```

If the corpus is stored elsewhere:

```bash
python scripts/audit_recipe_funnel.py \
  --recipe-data-root D:/mealpilot/recipe-data \
  --output .runtime/reports/recipe-funnel.json
```

The command is read-only. It never calls `ReviewStore.update()`, never changes
review versions, never publishes records, and never promotes a recipe to the
Solver.

## Reading the result

The optimization order is deliberate:

1. Fix **publication** blockers first, ranked by affected recipe count.
2. Only after a record becomes publishable, address **Solver** data gaps.
3. `DETERMINISTIC_OR_LLM_ASSIST` means a bounded structuring improvement may be
   appropriate in a later milestone.
4. `TRUSTED_DATA_ENRICHMENT` means the missing fact must come from a trusted
   source or reviewer evidence; the LLM must not invent it.
5. `HUMAN_REVIEW` means the condition should not be auto-repaired without a
   new, explicitly reviewed rule.

M40-F is a measurement milestone. The first automatic repair milestone after
it should target only the highest-frequency publication category shown by a
real-corpus report, with before/after funnel evidence and bounded regression
tests.
