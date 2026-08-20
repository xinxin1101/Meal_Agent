# Agent workflow (M2)

The M2 orchestrator runs as a bounded, in-process workflow. Pause/resume, repair options and replayable events are persisted by the durable run service; there is no second in-memory negotiation state store. The planner may only extract/classify constraints, invoke registered deterministic tools, choose a bounded server-verified repair action, and explain outcomes. It may not calculate authoritative nutrition, waive safety constraints, or grant itself crawler/shell permissions.

Flow: `Analyze -> Retrieve -> Candidate Guardrail -> Solve -> Validate -> Finalize`; the only M2 repair is `EXPAND_RETRIEVAL`, which retries once with the full already-safe local corpus. It never relaxes a constraint. State stores ids, structured summaries, data versions, reports, retry/tool budgets, and evidence refs—not recipe prose.

## M17 bounded three-role workflow

The user-approved workflow is `Planner Agent -> Retrieve -> Deterministic Solver + Validator -> Explanation Agent`. The Planner Agent may use SiliconFlow to extract only explicit time, protein, and energy observations and create a retrieval query. It never receives a health profile, cannot change the structured request, and cannot select recipes. The Decimal validator is the non-LLM authority for allergens, avoidances, nutrition, time, and Solver eligibility. The Explanation Agent runs only after a valid plan exists and receives only verified meal titles and totals; it cannot change the plan. Price, budget, and structured equipment are outside every agent contract.

If the LLM is disabled or fails, the Planner Agent uses the deterministic parser and the Explanation Agent is omitted. This preserves an offline, deterministic fallback.

## Phase 2 replayable observability

Durable runs persist safe progress events for `analyze`, `retrieve`, `solve`, optional `repair`, `validate`, and `finalize` while the workflow executes. Events contain only node outcome and `safe_payload`; they never contain the health profile, prompts, secrets, or hidden reasoning. The SSE endpoint assigns each event a monotonically increasing id, accepts both `Last-Event-ID` and `last_event_id`, and remains open until the run reaches `PAUSED`, `COMPLETED`, or `FAILED` and all stored events have been delivered. Clients reconnect from their last processed id and refresh the authoritative run snapshot whenever the stream disconnects.

## Phase 3 negotiation UI contract

The client renders a negotiation panel only for an authoritative `PAUSED` snapshot with a server-issued `NegotiationProposal`. It translates fields into user-facing budget, time, protein, and energy labels, but submits only the selected `option_id`, current `expected_run_version`, and an automatically generated idempotency key. On HTTP 409 it displays a stale-version notice and refreshes the snapshot. A successful decision keeps the same `run_id`; the SSE client reconnects from its last event id and appends the resumed execution to the existing timeline.

When the deterministic solver reports `CONSTRAINTS_INFEASIBLE`, the durable run service probes bounded adjustments to budget, time, protein, and energy range. Only options that produce a validated plan are shown. The user submits an option id; no client-provided field/value is trusted. Safety constraints are not options. `POST /v1/runs` may reach `PAUSED`; `POST /v1/runs/{run_id}/decisions` resumes the same persisted run with OCC and idempotency. The pre-M4 `/v1/negotiation-runs` in-memory path has been removed so there is only one state authority.

Users may cancel a `QUEUED`, `RUNNING`, or `PAUSED` run through `POST /v1/runs/{run_id}/cancel` with `expected_run_version` and an idempotency key. `CANCELLED` is terminal and is replayed through SSE.

## M21 adopted-history feedback loop

When `user_id` and `use_history=true` are explicit, at most ten recent adopted plans produce deterministic recipe occurrence counts. Retrieval uses those counts only after text relevance, and CP-SAT uses them as the fourth lexicographic objective after energy deviation, cost, and within-day repetition. History therefore cannot weaken candidate safety filters, hard constraints, nutrition, time, budget, Decimal validation, or the higher-priority objectives. Same request, data versions, and history snapshot remain deterministic.

Chat receives only the user-authorized current plan, adopted-history snapshots, and explicit preference memory. The LLM is told that adoption does not imply liking. The deterministic fallback supports bounded history summaries and always returns the exact history references used.

## M24 persistent conversation workflow

The browser creates or reopens a user-scoped conversation before sending a message. It submits the current conversation version and an idempotency key. The API resolves only explicitly selected `history_plan_ids`, obtains opt-in preference/current-plan context, and may pass at most the ten most recent stored dialogue messages to the optional LLM. After a reply is produced, the user and assistant messages are committed atomically with the exact evidence snapshot and the conversation version increments once. A stale version returns HTTP `409`; the client reloads the authoritative conversation before retrying.

Conversation text is context data, never an instruction that can change tool permissions, validation authority, or safety boundaries. Persisted conversation history does not infer allergies, health status, or long-term preferences.
