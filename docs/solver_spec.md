# Deterministic solver contract (M1)

## Variables and hard constraints

For each candidate recipe `r` and slot `s`, `x[r,s]` is binary (chosen or not) and `p[r,s]` is one of `{0.5, 1.0, 1.5, 2.0}` when chosen. Each requested slot selects exactly one compatible, `solver_eligible=true` recipe. Ingredients with an allergen, explicit avoidance, religious restriction, or uncertain composition are removed before solving. Energy range, declared macro minima/maxima, and total time are hard constraints in priority order:

`allergen safety > prohibitions > energy > macro targets > time`.

Allergen/prohibition constraints are never relaxed. Time, energy ranges, and nutrition targets are negotiable only through a separately confirmed constraint-set version. Cuisine, spiciness, explicit feedback, and repetition are soft. Price and equipment never enter candidate filtering, constraints, validation, or negotiation.

## Arithmetic and result states

Authoritative values are Decimal and are independently recalculated after solve. CP-SAT receives integers: energy `kcal*10` and macros `g*100`. Constraint contributions are conservatively rounded in the direction that prevents false feasibility. Inputs are rejected if their scaled products could exceed int64.

Only authoritative per-serving recipe nutrition enters CP-SAT. A qualitative ingredient never receives an assumed mass. A recipe with an unresolved nutritionally material qualitative ingredient is publication-only unless it has reviewed/source-declared per-serving nutrition.

Status is one of `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, or `UNKNOWN`. At 5 seconds, return `FEASIBLE` only if CP-SAT found a solution; otherwise return `UNKNOWN`, never a false infeasibility claim. Sort candidates by recipe id and use a fixed solver seed/single worker in M1 so identical inputs and versions produce identical output.

## Objective

After hard constraints, minimize lexicographically: energy deviation, within-day repetition, explicit user-feedback penalty, then recent-history repetition. Stable recipe-id ordering breaks any remaining tie. The implementation must preserve this reviewed order and may not introduce arbitrary weights.
