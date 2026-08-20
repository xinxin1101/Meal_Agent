# Domain contracts — contract v2

The executable source of truth is `backend/mealpilot/domain/models.py`. Public models use `extra="forbid"`: new API requests containing retired price, budget, or equipment fields receive HTTP 422. Persisted v1 JSON is adapted only at trusted storage boundaries by `domain/contract_migration.py`.

All authoritative numbers are JSON decimal strings and Python `Decimal`. Energy uses `kcal`, macros and measured solid mass use `g`, liquid source quantities remain `ml` until a density-backed conversion, and time uses minutes. Every source recipe carries source id, URL, licence, and data version. Calculated artifacts carry numeric-policy and nutrition-data versions.

## Ingredient quantity

Quantity is a tagged union implemented by `RecipeIngredient`:

```json
{
  "canonical_id": "beef",
  "canonical_name": "牛肉",
  "display_quantity": "200克",
  "quantity_kind": "MEASURED",
  "amount_g": "200",
  "nutrition_calculation_role": "INCLUDED",
  "allergens": [],
  "allergen_composition_known": true
}
```

```json
{
  "canonical_id": "spring-onion",
  "canonical_name": "葱花",
  "display_quantity": "少许",
  "quantity_kind": "QUALITATIVE",
  "amount_g": null,
  "nutrition_calculation_role": "EXCLUDED_MINOR_INGREDIENT",
  "allergens": [],
  "allergen_composition_known": true
}
```

- `MEASURED` requires positive `amount_g` and preserves the original display text.
- `QUALITATIVE` accepts reviewed `适量` or `少许`, requires `amount_g=null`, and never receives a guessed mass.
- Only a qualitative, nutritionally minor ingredient may be marked `EXCLUDED_MINOR_INGREDIENT`.
- Oils, sugars, fatty meat, nuts, sesame, and energy-dense sauces are nutritionally material and cannot be excluded merely because their source quantity is qualitative.
- Ingredient identity and allergen composition must be known in both modes.

## Recipe eligibility

`Recipe` stores ordered, image-independent `CookingStep` text and ingredient references. Equipment may appear naturally inside instructions but has no structured field or planning meaning.

Publication and Solver eligibility are deliberately separate:

- `PUBLICATION_READY`: provenance/licence, ingredient identity, servings, time, and cooking steps pass display review. Qualitative quantities and unknown composition may be retained with an explicit warning, but the recipe is forced to `solver_eligible=false`.
- `SOLVER_READY`: all publication rules pass and `nutrition_per_serving` has one authoritative `nutrition_basis`: `CALCULATED_FROM_INGREDIENTS`, `SOURCE_DECLARED`, or `REVIEWED_STANDARD_PORTION`.
- A publication-only recipe may be shown as cooking reference but is excluded from planning and post-solve totals.

## Planning contracts

```json
{
  "UserProfile": {
    "profile_snapshot_id": "profile-001",
    "adult_confirmed": true,
    "age_years": 28,
    "nutrition_parameter_sex": "female",
    "height_cm": "165",
    "weight_kg": "58",
    "activity_level": "moderate",
    "goal": "maintain",
    "allergens": ["peanut"],
    "avoidances": ["shellfish"]
  },
  "MealPlanningRequest": {
    "request_id": "request-001",
    "meal_slots": ["breakfast", "lunch", "dinner"],
    "persons": 1,
    "max_total_minutes": 60,
    "energy_kcal_range": {"min": "1800", "max": "2200"},
    "protein_min_g": "90",
    "numeric_policy_version": "contract-v2-decimal-v1"
  }
}
```

Each new `PlanSelection` contains the recipe id/version plus a read-only title and ingredient display snapshot, so the UI can render `牛肉 200克`, `葱花 少许`, or `盐 适量` without looking up mutable source data. `PlanTotals` contains energy, protein, carbohydrate, fat, and preparation minutes. `ValidationReport` contains those totals plus validator and nutrition-data versions. `NegotiationProposal` may only offer `max_total_minutes`, `protein_min_g`, or `energy_kcal_range`.

Planning now has two deliberately non-interchangeable result contracts:

- `MealPlan` is the original deterministic nutrition-validated result. It only uses `SOLVER_READY` recipes and may claim that nutrition, time, avoidance, and declared-allergen validation passed.
- `MenuDraft` has status `UNVERIFIED_MENU` and requires `acknowledge_unverified=true`. It may arrange published display recipes but never claims nutrition or medical validation. `nutrition_totals` is `null` unless all three selected recipes carry an authoritative nutrition basis; individual meal nutrition is likewise optional. It carries `menu-draft-v1`, `menu-draft-decimal-v1`, exact source metadata, nutrition-data versions, and machine-readable warnings.

Declared allergies remain fail-closed in both modes: a display recipe with unknown allergen composition is excluded from a draft whenever the profile declares any allergen. With an explicitly confirmed empty allergy list, such a recipe may appear only in the visibly unverified draft and carries `ALLERGEN_COMPOSITION_NOT_FULLY_REVIEWED`; it can never enter `MealPlan`.

## Accounts, privacy, history, and conversations

Registration, signed access tokens, rotating refresh sessions, owner-scoped resources, profile OCC, export, and permanent account deletion retain their M23 contracts. The authenticated identity is authoritative; submitted/path user ids cannot grant ownership.

`AdoptedMealPlan` is an immutable snapshot made only from an owned completed run with a valid deterministic report. It stores meal titles, nutrition/time totals, the original time/energy/protein constraints, validation evidence, and recipe/nutrition/numeric versions. History mutation remains idempotent and OCC-versioned.

Conversation messages persist the exact current plan, adopted history, preference memories, and response source used. Old free-text messages mentioning cost or equipment remain historical text but are never parsed into a new plan. Preference categories are only `food_preference`, `avoidance`, and `cooking_style`; behaviour never infers allergy or health state.

## Acquisition and review

`RawMeishiChinaRecipe` is untrusted quarantine data, never a planning recipe. `StructuredRecipeDraft` preserves raw quantities, reviewed quantity kind, normalized identity, allergen state, ordered text steps, servings, slots, time, and optional authoritative nutrition. New `RecipeQualityReport` artifacts use `mc-r3-v3` (v2 remains readable for audit history) and report `BLOCKED`, `PUBLICATION_READY`, or `SOLVER_READY` with separate publication and Solver blockers. The administrator catalog projection exposes both `blocking_reasons` and `solver_blocking_reasons`, so a successfully published display recipe is never confused with a planning-ready recipe.

Authorization evidence is bound to exact source id and content hash. Personal-study acknowledgement never satisfies publication rights. Approval additionally requires deterministic quality and delegates to the existing `publish()` boundary; revocation withdraws the exact version.

## Retired fields

The following must not appear in an active API, domain model, Solver input, validation result, history snapshot, preference, or new persisted record: `max_cost_cny`, `estimated_cost_cny`, `cost_cny`, `price_snapshot_id`, price catalog models, `UserProfile.equipment`, `Recipe.required_equipment`, and `CookingStep.equipment_refs`.
