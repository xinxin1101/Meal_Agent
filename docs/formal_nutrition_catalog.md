# Formal nutrition catalog gate

MealPilot keeps repository nutrition samples outside the production trust boundary. The runtime formal catalog lives at:

```text
<MEALPILOT_RECIPE_DATA_ROOT>/nutrition/foods.json
```

`data/nutrition/foods.sample.json` is a deterministic test fixture only. Runtime and acquisition code must never silently fall back to it.

## Candidate contract

A candidate is a JSON array of `FoodNutrition` records. Each record must provide:

- a stable `canonical_id` and `food_data_id`;
- non-negative nutrition per 100 g;
- source id, HTTP(S) source URL, licence string, and data version;
- no repository test-only licence (`internal-development-only`);
- no `.invalid` test source URL;
- unique canonical and food-data ids across the candidate.

The formal gate intentionally does not infer or repair missing values. Invalid data is rejected before runtime state is changed.

## Review and install workflow

First inspect the candidate without changing runtime state:

```powershell
python scripts/import_nutrition_catalog.py --input C:\path\to\foods.candidate.json
```

The command prints a SHA-256 digest plus the record/source/version summary. Review the exact file identified by that digest. Installation then requires the digest and an explicit human-review acknowledgement:

```powershell
python scripts/import_nutrition_catalog.py `
  --input C:\path\to\foods.candidate.json `
  --expected-sha256 <reviewed-candidate-sha256> `
  --install `
  --confirm-reviewed
```

The installed bytes are identical to the reviewed candidate bytes. The write uses a same-directory temporary file followed by `os.replace`, so readers never observe a partially written JSON file.

## Replacement / optimistic concurrency

Replacing an existing formal catalog additionally requires the SHA-256 of the current installed file:

```powershell
python scripts/import_nutrition_catalog.py `
  --input C:\path\to\foods.next.json `
  --expected-sha256 <reviewed-next-sha256> `
  --expected-current-sha256 <currently-installed-sha256> `
  --install `
  --confirm-reviewed
```

If another process or administrator changed the installed catalog after it was reviewed, the current hash no longer matches and the replacement is rejected. This is the file-level equivalent of optimistic concurrency control used elsewhere in MealPilot.

## What this gate does not authorize

Passing this gate only means the food nutrition records are structurally valid, explicitly sourced, review-confirmed, and hash-bound. It does **not** automatically make a recipe Solver-eligible. A recipe still requires trusted ingredient quantities, known allergen composition, a valid nutrition basis, publication/review state, and the existing deterministic catalog/Decimal validation gates.

Do not copy the repository sample nutrition file into the formal path and do not use an LLM to guess grams, allergens, nutrition values, source licences, or data versions.
