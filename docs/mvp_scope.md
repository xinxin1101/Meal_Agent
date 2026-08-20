# MVP scope

## In scope

MealPilot accepts a confirmed healthy-adult profile and one planning request, then uses **local structured, Solver-eligible recipes** to select exactly one breakfast, lunch, and dinner. It validates allergens, explicit avoidances, total energy/macros, and preparation time. It returns either a plan plus a validation report, or a typed, structured reason.

An explicitly selected secondary mode may arrange three **published display recipes** as an `UNVERIFIED_MENU`. This is a cooking menu draft, not a nutrition plan: it does not claim energy/protein/time validation, does not invent missing nutrition, and cannot be adopted into verified history. Its UI and API contract must remain visibly distinct from `MealPlan`.

## Explicitly out of scope

- Week plans, households, inventory mutation, or shopping checkout.
- Disease-specific diets, diagnosis, treatment, or advice for minors, pregnancy, severe chronic illness, or eating disorders.
- Unbounded crawling or automatic publication without an authorization whitelist and deterministic quality gates.
- Multi-agent execution, model routing, semantic caching, solver clusters, and generative UI.

## Safety gate

The profile must state adult eligibility, age, nutrition calculation parameter, height, weight, activity level, goal, allergies, and avoidances. Missing fields are rejected at the API boundary. Missing or uncertain allergen composition is unsafe for a precise plan and must produce `SAFETY_PROFILE_INCOMPLETE` or `UNKNOWN_ALLERGEN_COMPOSITION`; it must never be treated as safe. Price, budget, and available-equipment planning are not part of this product contract.
