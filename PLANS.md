# MealPilot delivery plan

## Frozen MVP

Generate a one-day breakfast/lunch/dinner plan for a healthy adult from local, structured, Solver-eligible recipes. Validate nutrition, allergens, avoidances, and total preparation time; return structured reasons when no plan exists. Price, budget, and structured equipment planning are explicitly retired in contract v2.

Excluded from MVP: weekly or household planning, clinical diets, production crawling, multi-agent orchestration, semantic caching/model routing, distributed solvers, generative UI, and actual inventory deduction.

## Milestones

| Milestone | Scope | Done when |
|---|---|---|
| M0 | Repository, contracts, sample data, test harness | App starts and sample recipes validate/import. |
| M1 | Deterministic core | Fixed input produces a validated plan or typed failure without LLM/vector DB. |
| M2 | RAG + agent loop | Constraints can be extracted, candidates retrieved, solved, validated, retried. |
| M3 | Interrupt/resume | An infeasible request can pause and resume after an approved change. |
| M4 | Async execution | Long runs, SSE, OCC, idempotency, and recovery tests pass. |
| M5 | Product delivery | UI, Docker, evaluation, and demo loop are complete. |

## Current milestone: contract-v2 domain slimming implemented; M22 formal recipe corpus remains blocked on approved records

RQ-1 through RQ-8 (user-approved): remove price/budget/equipment end-to-end; add measured/qualitative ingredient quantities; split publication and Solver eligibility; migrate stored documents with backup/compatibility; update backend, agents, history/chat, frontend, tests, data, and documentation. [complete 2026-08-14; 405 SQLite JSON documents migrated after backup, with a zero-change verification and read-time compatibility retained]

AUTO-R1 through AUTO-R4: policy-bound incremental discovery, source-id/content-hash state, bounded raw quarantine, automatic Pydantic/mc-r3-v2 review preparation, local operator workbench, and conditional auto-publication requiring an explicit source whitelist plus valid hash-bound evidence and `SOLVER_READY`. [automation code and offline tests complete 2026-08-14; policy ships disabled and current MeishiChina records cannot auto-publish]

M18 (user-approved): local, user-controlled preference memory and a non-medical chat interface. [complete]

M19 (user-approved): repository-level MediaCrawler recipe acquisition Skill, pinned and bounded crawler wrapper, and raw-quarantine JSON/JSONL adapter. This is a non-commercial development data-entry path, not production crawling and not direct database publication. [complete]

M22 (user-approved): licensed formal recipe corpus plus cooking steps, seasoning, substitutions, per-meal metrics and shopping-list delivery. MC-R1/MC-R2 provide a bounded personal-study MeishiChina HTML parser/importer, ten offline fixtures, no-image acquisition, robots/rate-limit/access-challenge hard stops, and raw quarantine staging. MC-R3 v2 adds deterministic ingredient/unit/allergen/step/serving/time/nutrition gates, qualitative quantities, and separate publication/Solver readiness. MC-R4 adds versioned review, exact-source authorization evidence, approve/reject/revoke, publisher delegation, and a published-only Solver data entry. [tooling complete 2026-08-14; live study records remain pending without qualifying authorization]

M20 (user-approved): UI-R1 through UI-R5 frontend redesign with an application shell, planning-first composer, explicit allergen confirmation, user-facing progress, verified meal result cards, negotiation differences, plan-linked chat, preference/profile workspace, responsive accessibility checks, and regression verification. [complete]

M21 (user-approved): explicit plan adoption, immutable/versioned user history, item/collection deletion, history-aware chat with evidence controls, and deterministic lower-priority history diversity for future plans. [complete]

M24 (user-approved): durable Conversation/Message storage, conversation list and refresh recovery, exact adopted-history selection, and persisted per-answer evidence. [complete]

M23 (user-approved): email/password registration and login, short-lived signed access tokens, rotating hashed refresh sessions, authenticated server identity, strict ownership on memory/history/feedback/conversations/chat/runs/SSE, versioned server profile, complete account export, permanent account/data deletion, retention cleanup, production secret enforcement, and multi-user authorization tests. [complete 2026-08-13]

M25 (user-approved): explicit completed/skipped/replaced/rating feedback and explicit reuse/avoid directives. Only submitted feedback affects the lower-priority deterministic recipe ordering. [complete]

M26 (user-approved): 10-case deterministic evaluation corpus, frontend unit/accessibility tests, Playwright critical-flow tests, and GitHub Actions for backend, frontend, SSE/negotiation/history flows, and production adapters. [complete]

M27 (user-approved): optional PostgreSQL stores and SQL migrations, leased PostgreSQL run queue, transactional run-event Outbox, Redis stream publisher, production Compose, rate limiting, Prometheus metrics, readiness, backup/restore scripts, and Caddy HTTPS gateway. [local Docker integration verified 2026-08-13: migration, queue/worker, expired-lease recovery, Outbox-to-Redis, user isolation, metrics, backup and isolated restore passed; licensed recipe review remains a separate beta gate]

M28 (user-approved hardening): versioned healthy-adult nutrition target suggestions requiring explicit confirmation; durable run cancellation; removal of the obsolete in-memory negotiation state path; PostgreSQL worker lease heartbeat; scheduled production retention and backups; expanded frontend destructive-action and suggestion tests. [implemented and container builds verified 2026-08-13; nutrition policy still requires qualified domain approval before beta]

M29 (user-approved): migrate legacy MC-R3 review records with timestamped backup; add bounded SiliconFlow curation proposals followed by Pydantic and deterministic revalidation; introduce USER/ADMIN roles and a role-protected read-only recipe catalog. [implemented 2026-08-14; migrated records remain PENDING/BLOCKED and no LLM suggestion can approve or publish]

M30 (user-approved): remove prototype recipe data from runtime, keep sample recipes test-only, and add an ADMIN-only review workbench for LLM assistance, raw/structured comparison, trusted mappings, serving/slot/time/step correction, deterministic revalidation, authorization evidence, approval, and publication. [implemented 2026-08-14; runtime recipe catalog reset to zero with recoverable backup]

M31-M35 (user-approved): one configurable persistent recipe-data root; API/Worker Docker volume; infrastructure versus business readiness; idempotent/OCC leased acquisition jobs executed only by a standalone Worker; ADMIN-only preview/trigger/status UI. [implemented 2026-08-14; source policy remains disabled by default, no live acquisition was run, and approval/publication remain explicit human actions]

M36 (user-approved simplified review): acquisition now runs deterministic initial validation, bounded LLM display structuring, then Pydantic and deterministic final validation. `SOLVER_READY` records enter an administrator batch-review list; one confirmation approves and publishes them to the internal personal-study catalog. The active UI and API no longer contain authorization-evidence registration. Source URL, capture time and content hash remain mandatory provenance, and this internal workflow does not grant public redistribution rights. [implemented 2026-08-14; 10 existing records remain preserved and await an explicit administrator-triggered LLM pass]

M37 (user-approved recipe-blocker correction): `mc-r3-v3` corrects deterministic Chinese action-step false positives, excludes source notes/presentation/ingredient-label pseudo-steps, and accepts only source-explicit mass/OCR spelling corrections. Runtime pending reviews were backed up and revalidated; six records became `PUBLICATION_READY`. Administrator catalog responses and UI now separate page-publication blockers from Solver blockers. Nutrition target estimation guides an unspecified profile to select female/male parameters before calling the API. [implemented 2026-08-15; four published MeishiChina recipes remain display-only and Solver count remains zero because their qualitative quantities, allergen composition, and authoritative nutrition basis are incomplete]

M38 (user-approved dual planning mode): retain the strict Solver-backed nutrition plan and add an explicitly unverified ordinary menu draft over published display recipes. Drafts never invent or display missing nutrition, never enter verified history/chat context, and keep declared allergies fail-closed. [implemented 2026-08-15; current five published records can create a three-meal draft for a profile that explicitly confirms no known allergies, with breakfast/time/allergen-review warnings]

R39 (user-approved layered recipe admission): implement a three-tier `READABLE` → `MENU_DRAFT_ELIGIBLE` → `SOLVER_ELIGIBLE` publication path. Source-backed recipes with actionable steps can be batch-published to the readable library even when nutrition, exact quantities, or slots are incomplete; menu drafts additionally require real breakfast/lunch/dinner coverage and never relabel a recipe to fill a slot; `SOLVER_READY` remains mandatory for nutrition planning. Product readiness, administrator catalog, authenticated recipe-library API, planning UI, capability migration, and regression tests enforce the separation. [complete 2026-08-25]

M5 frontend hardening plan (user-approved): Phase 1 API architecture and profile state [complete]; Phase 2 SSE observability [complete]; Phase 3 negotiation UI [complete]; Phase 4 chat/memory UI [complete]; Phase 5 Docker/product delivery [complete; container build, health checks, Nginx API proxy, and SSE durable-run smoke test passed on 2026-08-11].

1. Establish the contracts and solver specification. [complete]
2. Provide a versioned local recipe fixture and import validation. [complete]
3. Provide a minimal API health endpoint and test command. [complete]
4. Implement candidate selection, solver and validation without LLM/vector DB. [complete]
5. Add bounded LangGraph orchestration, local retrieval, constraint extraction, and retry. [complete]
6. Add LangGraph interrupt/resume negotiation with confirmed constraint changes. [complete]
7. Add asynchronous runs, durable events, SSE replay, OCC, and idempotency. [complete]
8. Add demo UI, Docker Compose, and reproducible fixed-scenario evaluation. [complete]
9. Add a trusted recipe ingestion/publish pipeline and versioned data quality gates. [complete]
10. Add versioned nutrition and deterministic quantity-normalization data layers. [complete; price portion retired by contract v2]
11. Recalculate recipe nutrition from catalog data where coverage is complete. [complete, M8 contract-v2 revision]
12. Add a pinned nutrition-catalog coverage quality gate and read-only inspection endpoint. [complete, M9 contract-v2 revision]
13. Guard every planning API entry point against an incomplete local catalog snapshot. [complete, M10]
14. Complete the healthy-adult profile contract. [complete, M11; equipment dimension retired by contract v2]
15. Remove the obsolete price-snapshot binding from every planning request. [complete, contract v2]
16. Materialize nutrition Solver inputs from catalog snapshots and reject unsafe integer ranges. [complete, M13 contract-v2 revision]
17. Expose redacted run audit summaries. [complete, M14]
18. Add version-pinned fixed-scenario evaluation. [complete, M15]
19. Complete frozen-MVP delivery verification and record the production Roadmap. [complete, M16]
20. Add a bounded MediaCrawler acquisition Skill and preserve the raw-quarantine publication boundary. [complete, M19]
21. Replace the phase-stacked demo UI with the three-page MealPilot planning workspace and verify desktop/mobile delivery. [complete, M20]
22. Complete the adopted-plan history and feedback loop without inferring preferences or weakening hard constraints. [complete, M21]
23. Persist user-scoped conversations and restore exact answer evidence across browser refreshes. [complete, M24]
24. Add explicit meal feedback that can only influence post-safety soft ordering. [complete, M25]
25. Automate deterministic, frontend, accessibility, E2E, and CI quality gates. [complete, M26]
26. Add the opt-in PostgreSQL/queue/Outbox/backup/monitoring/HTTPS production runtime foundation. [implemented, M27]

## Open decisions with defaults

| Decision | Default until changed |
|---|---|
| Backend runtime | Python 3.11 + FastAPI + Pydantic v2 (current `.venv`). |
| Solver | OR-Tools CP-SAT, added at M1. |
| Meals | Exactly one recipe per breakfast, lunch, and dinner. |
| Portions | 0.5, 1.0, 1.5, or 2.0 recipe servings. |
| Objective order | Safety, prohibitions, energy, nutrition, time, within-day repetition, explicit feedback, then history diversity. |
| Price and equipment | Not represented in active contracts, Solver, validation, history, chat evidence, or UI. |
| Solver timeout | 5 seconds; return FEASIBLE if found, UNKNOWN otherwise. |
| Determinism | Stable recipe-id ordering and single solver worker for M1. |
| Multi-Agent workflow | User-approved Planner Agent, deterministic validator, and Explanation Agent; model outputs never override validation. |
