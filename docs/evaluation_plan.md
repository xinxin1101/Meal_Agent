# Evaluation and acceptance

## Contract v2 (RQ-1–RQ-8)

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\evaluate.py
Set-Location frontend
pnpm test
pnpm build
```

Acceptance requires:

- public API rejects retired price/budget/equipment fields;
- measured and qualitative ingredient contracts validate deterministically;
- qualitative quantities never receive guessed mass or nutrition;
- nutritionally material unresolved quantities cannot enter the Solver;
- publication and Solver eligibility are independently tested;
- allergen and avoidance handling remains fail-closed;
- identical requests and data versions return identical plans;
- negotiation offers only time, protein, and energy adjustments;
- history, conversation, memory, SSE, feedback, auth, and deletion suites pass;
- old SQLite profile/run/history JSON opens through the trusted compatibility boundary;
- TypeScript strict checking, frontend tests, accessibility checks, and the production Vite build pass.

Preview the persisted-data migration with:

```powershell
.\.venv\Scripts\python.exe scripts\migrate_contract_v2.py
```

Stop application writers before applying it. `--apply` must first create `.runtime/backups/contract-v2-<UTC timestamp>/`; validate all accounts, runs, histories, conversations, and memories before deleting any backup.

## Planning and agents

The deterministic suite covers feasible days, repeatability, allergen rejection, unknown composition, candidate shortage, energy/time/protein infeasibility, CP-SAT `UNKNOWN`, Decimal post-validation, and int64 preflight. M1 runs without LLM, vector database, queue, or external nutrition service.

The bounded agent suite verifies time/protein/energy extraction, local retrieval, LangGraph node trace, one retrieval expansion without constraint relaxation, deterministic validation, and explanation only after a valid result. Durable runtime checks cover pause/resume, OCC, idempotency, cancellation, SSE replay, recovery, and ownership.

The dual-mode suite additionally verifies that three published display recipes can produce a deterministic `UNVERIFIED_MENU`, missing nutrition yields `nutrition_totals=null`, complete authoritative nutrition is summed with version evidence, and declared allergies exclude unknown-composition recipes. The frontend must hide energy/protein controls and verified badges in draft mode and must not offer adoption for a draft.

## Recipe ingestion

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_meishichina_importer.py tests/test_meishichina_quality_review.py -q
```

Ten offline pages must reliably yield title, original ingredients, and ordered image-independent steps. Acquisition must prove bounded collection, no images, robots/rate-limit/challenge hard stops, hash deduplication, and raw quarantine.

Quality tests cover explicit grams, `适量`, `少许`, unsupported ambiguous phrases, materially important qualitative ingredients, minor seasoning exclusion, allergens, medicalized text, and `BLOCKED/PUBLICATION_READY/SOLVER_READY`. Approval additionally requires a completed LLM-structure stage and final `SOLVER_READY` validation; batch publication is idempotent and revocation withdraws only the exact version.

Automation tests run two identical offline acquisitions: the first stages and prepares review items, while the second reports unchanged hashes and creates no batch. Even with an auto-publish flag and source-id whitelist, missing hash-bound authorization must stop publication.

M31-M35 additionally run `tests/test_recipe_data_settings.py` and `tests/test_recipe_acquisition_jobs.py`: every acquisition artefact resolves below one root; job creation is payload-bound and idempotent; cancellation uses OCC; worker tests inject offline executors; disabled policies cannot queue; unknown exception text is redacted. Acceptance tests never perform live crawling.

## Product and production runtime

Frontend tests cover navigation, safety text, planning/SSE, infeasible negotiation, plan adoption/history, persistent chat evidence, memory, explicit feedback, deletion confirmation, responsive layout, and accessibility. Docker acceptance uses `docker compose config --quiet` and the configured development/production smoke checks.

PostgreSQL/Redis acceptance separately verifies migrations, user isolation, queue leases and heartbeat, cancellation, transactional Outbox, Redis publication, retention, metrics, rate limiting, backup, and isolated restore. HTTPS and licensed recipe data remain independent beta gates.
