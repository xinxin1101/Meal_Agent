# Architecture and staged boundaries

M0 is a Python/FastAPI contract shell with versioned local JSON fixtures. M1 adds pure deterministic modules: candidate filtering, coverage guard, Decimal calculation, CP-SAT adapter, and validator. No domain module may depend on FastAPI.

M2 introduces LangGraph only as an orchestrator around those deterministic tools. M3 adds interrupt/resume. M4 provides a single-node development runtime: SQLite state/events, FastAPI background execution, SSE replay, `run_version` OCC, and idempotency. PostgreSQL, Redis Streams, leases, and separate workers remain the production evolution path. M5 adds React/Vite, Docker services, fixed-scenario evaluation, and deployment polish.

Defaults awaiting explicit review are recorded in `PLANS.md`. The source of truth for choices that affect a generated plan is a versioned request/profile/recipe/nutrition snapshot and `numeric_policy_version`. Contract v2 has no price snapshot or structured equipment dimension.

## Dual planning boundary

`POST /v1/runs` and `POST /v1/meal-plans/deterministic` remain the verified nutrition path and continue to require business-ready Solver coverage. `POST /v1/menu-drafts` is a separate authenticated, side-effect-free deterministic path over the published display catalog. It does not enter LangGraph, CP-SAT, durable-run history, adoption, or verified-plan chat context. This separation prevents an unverified cooking menu from being persisted or described as a validated meal plan.
## M23 identity and privacy boundary

The API derives the authoritative user ID from a signed 15-minute access token. Browsers also receive the access token as a SameSite Strict HttpOnly cookie so native `EventSource` can authenticate SSE; API clients may use the Bearer header. A 30-day opaque refresh token is stored only in a scoped SameSite Strict HttpOnly cookie, while the database stores only its SHA-256 hash. Refresh is single-use rotation and logout revokes the active session. Passwords use salted `scrypt` hashes.

All user-scoped endpoints verify authenticated ownership, including indirect identifiers such as run, conversation and adopted-plan IDs. The client-provided `user_id` is never authoritative. Production startup fails without an explicit secret of at least 32 characters; local development persists a generated secret under `.runtime`. Account deletion physically removes credentials, profile, memory, adopted plans, feedback, conversations and runs. Revoked refresh sessions are retained for 30 days and terminal-run operational events for 90 days, enforced by `scripts/purge_retention.py`.
