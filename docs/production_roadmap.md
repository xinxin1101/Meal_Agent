# Delivery roadmap after the frozen local MVP

## Completed local MVP

The repository now supports a healthy adult's one-person breakfast/lunch/dinner plan using local structured recipes. It has fail-closed allergen and avoidance checks, Solver-eligibility filtering, CP-SAT with Decimal revalidation and int64 preflight, catalog-derived nutrition inputs, bounded orchestration, interrupt/resume, SQLite-backed development runs, SSE replay, OCC, idempotency, a product UI, data publication gates, and fixed-scenario evaluation. Contract v2 deliberately removes price, budget, and structured equipment planning.

This is a development demonstration. Its five recipes, nutrition records, and prices are explicitly internal development fixtures, not medical or production data.

## Required before a beta with real users

1. Obtain an explicit licensed or first-party recipe dataset. Assign a data owner, source/licence review process, retention period, and versioning policy. The target 300–500 recipes must not be fabricated from unverified web content.
2. Complete qualified domain review of `healthy-adult-estimate-v1`. The application now exposes a versioned Mifflin–St Jeor/activity/goal estimate with an adult protein reference, but it remains advisory and requires an explicit user action before it changes request targets.
3. Complete deployment-specific secret rotation, privacy/legal review, email verification and account recovery. Authentication, owner isolation, export, deletion, and automated retention cleanup are implemented.
4. Expand the evaluation set from the current 10 regression scenarios to a reviewed corpus of at least 100 independent scenarios, with labelled safety, feasibility, retrieval, rounding, interruption, and recovery outcomes.

## Production engineering evolution

1. PostgreSQL, the leased queue, lease heartbeats, cancellation, and Outbox-backed replayable events are implemented. The obsolete in-memory negotiation graph was removed; the durable run store is the only pause/resume authority. Distributed execution and graceful in-flight solver interruption remain future work.
2. Authentication/authorization, rate limits and Prometheus metrics are implemented. Request budgets, alert rules, and OpenTelemetry-compatible tracing with profile redaction remain.
3. If hybrid retrieval is approved, add a versioned embedding index, dense retrieval, RRF, and reranking. Keep hard safety filters before and after retrieval.
4. CI, migration scripts, manual restore and scheduled backups/retention are implemented. Add load testing, automated restore drills, and deployment-specific secret management before any availability or throughput claim.

## Explicitly deferred product scope

Weekly/family planning, clinical or disease-specific recommendations, automatic inventory deduction, shopping checkout, generative UI, multi-agent orchestration, semantic full-plan caching, model routing, distributed solver clusters, and unrestricted crawling remain outside the frozen MVP.
