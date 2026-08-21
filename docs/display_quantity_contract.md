# Display-tolerant ingredient quantity contract

MealPilot separates **what may be shown to a user** from **what may be trusted by the nutrition Solver**. Real recipe pages frequently omit ingredient amounts, so absence of a safe numeric quantity is not by itself a reason to discard an otherwise usable cooking recipe.

## Quantity kinds

| Kind | Meaning | Example | Publication | Solver |
|---|---|---|---|---|
| `MEASURED` | A trusted quantity can be normalized to grams | `牛肉 200克` | yes | eligible if every other gate passes |
| `QUALITATIVE` | The source or reviewer explicitly supplies a qualitative amount | `盐 少许` | yes | only according to the existing minor-ingredient rules |
| `UNSPECIFIED` | No trusted gram quantity exists | missing amount, `若干`, `按需` | yes | **never** |

`UNSPECIFIED` never carries `amount_g` and never contributes to nutrition calculation.

## Quantity provenance

`quantity_origin` records why the displayed value exists:

- `SOURCE_EXPLICIT`: the displayed quantity comes from source text. This includes explicit measured values, `适量` / `少许`, and ambiguous source text such as `若干` or `按需`.
- `DISPLAY_FALLBACK`: the source contained no amount. MealPilot generated display text only; it did not create nutrition evidence.
- `REVIEWER_CONFIRMED`: an administrator explicitly supplied or corrected a quantity during trusted review.

Older stored recipes that do not contain this field remain readable and default to `SOURCE_EXPLICIT`.

## Deterministic display fallback

When the source amount is empty:

- `seasoning` -> display `适量`;
- `main`, `secondary`, or `other` -> display `用量未注明`.

The internal kind remains `UNSPECIFIED` and the origin is `DISPLAY_FALLBACK`. The fallback is therefore never misrepresented as source evidence.

When the source already contains ambiguous non-numeric text such as `若干`, `酌量`, `按需`, or `随意`, the original text is preserved and the origin remains `SOURCE_EXPLICIT`.

## LLM boundary

The recipe-structuring LLM remains source-bound:

- it may organize title, servings, meal slots, time, ingredients, and cooking steps;
- it may copy an explicit source quantity;
- it must not invent grams, volumes, allergens, nutrition, licences, approval, or publication state;
- it must use `适量` / `少许` as qualitative source facts only when those words are present in the source.

Missing-quantity display fallback is deterministic backend policy, not an LLM guess.

## Publication and Solver gates

A recipe with `UNSPECIFIED` quantities can become `PUBLICATION_READY` when all other page-publication requirements pass. It can then be used by the explicitly unverified `MenuDraft` path.

It cannot become `SOLVER_READY`. Nutrition materialization treats any included `UNSPECIFIED` ingredient as `NUTRITION_QUANTITY_INCOMPLETE`, including recipes that otherwise carry source-declared or reviewed-standard-portion nutrition. This prevents an authoritative-looking nutrition field from bypassing the ingredient trust boundary.

A later trusted correction may replace an unspecified value with a reviewer-confirmed measured quantity. The existing formal nutrition and reviewed Solver-promotion gates still decide whether the recipe can finally enter verified planning.
