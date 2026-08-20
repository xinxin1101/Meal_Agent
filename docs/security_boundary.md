# Health, privacy, and security boundary

MVP is for healthy adults. Do not create medicalized recommendations for minors, pregnancy, severe chronic conditions, or eating disorders. Do not infer allergy history. Omitted allergy information is not a safety clearance.

Only collect the profile fields required for planning: adult status, age, nutrition calculation parameters, height/weight, activity, goal, allergies, avoidances, and time availability. Redact those fields from logs and traces; retain only hashes or minimal safe summaries. Persistence provides export and deletion of profiles and planning history.

Recipe texts, LLM outputs, and tool failures remain untrusted until deterministic schema and rule validation. Tools have declared schemas, timeout/retry policies, idempotency, side-effect levels, and authorization scopes.

`SILICONFLOW_API_KEY` belongs only in the ignored local `.env` file or a deployment secret store. It must never appear in source, tests, traces, prompts, or API responses. The optional SiliconFlow adapter receives only a sanitized negotiation summary to word an explanation; it cannot select an option or change profile/constraint data.

## Authentication and account ownership (M23)

Every private API call is associated with an account created by email/password registration. Passwords are salted `scrypt` hashes. A signed access token expires after 15 minutes; the opaque 30-day refresh token is single-use rotated and only its SHA-256 hash is stored. Browser cookies are HttpOnly and SameSite Strict, and production cookies are Secure. State-changing private requests require the Bearer access token, while the access cookie exists only so same-origin SSE can authenticate.

The authenticated identity is authoritative. Any client `user_id` is either checked for equality or overwritten with the authenticated account id. Memory, history, feedback, conversations, chat evidence, durable snapshots, run audit, decisions and SSE all enforce ownership. The account owner can export all active data and permanently delete credentials, profile, memory, plans, feedback, conversations and runs. Revoked refresh records are retained for 30 days and terminal operational events for 90 days; `scripts/purge_retention.py` enforces this policy.

`MEALPILOT_AUTH_SECRET` must be supplied through the production secret store, must contain at least 32 characters, and must never be committed. Production startup fails closed when it is missing.

## Preference memory and chat

Preference memory is opt-in and account-scoped in SQLite or PostgreSQL. It stores only user-submitted food preferences, avoidances, and cooking styles. It never infers allergies, diagnoses, or health information from behaviour. `GET /v1/memory/{user_id}` exposes only the caller's saved record and `DELETE /v1/memory/{user_id}` permanently removes it. Chat uses only the submitted message and explicitly authorized plan/history/preferences; it must state non-medical boundaries and never claim a recommendation is safe.

## Adopted plan history

Only a plan explicitly adopted by its owning user enters history. Adoption is a historical fact, not an inferred preference. History never modifies allergies, avoidances, profile values, nutrition targets, budget, or time constraints. The user independently controls whether current plan, adopted history, and saved preferences enter a chat request; the response lists exact evidence ids used.

Deleting a plan removes it from active storage and idempotency response payloads, creates a minimal run-id tombstone to prevent replay resurrection, and immediately excludes it from chat and future planning. Logs and traces must not include history meal prose or profile fields.

## Persistent conversation data

The M24 runtime stores user and assistant message text plus the exact plan/memory/history evidence used. It does not persist raw profile fields, hidden reasoning, prompts, API keys, or model credentials. M23 removes the fixed demo identity and requires authenticated ownership at every conversation read and write. Export, deletion and retention are account controls; transport encryption remains the HTTPS gateway's responsibility.
