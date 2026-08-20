# Local memory and chat contract (M18)

## Interaction states

The chat panel must visibly show `idle`, `sending`, `reply`, or `error`. A user submission disables the input and button, shows a progress message, and times out client-side after 18 seconds. HTTP errors and timeouts render a readable retry message rather than silently dropping the request.

## API contract

`POST /v1/chat` accepts a local `user_id`, a message of 1-1000 characters, and an optional `plan_context`. `plan_context` contains only the currently validated plan id, three meal labels/portions, and authoritative totals; it never includes a health profile. It returns `reply`, `memories_used`, and `response_source` (`siliconflow` or `deterministic_fallback`). The server may fall back when the model is unavailable. The front end never treats an empty or non-200 response as a chat reply.

## Memory contract

Only explicitly submitted preference items are stored. The user may read or delete all items through `/v1/memory/{user_id}`. Chat must not infer allergies or health conditions, and it is not medical advice.

On page load the local record is shown in the preference input. When a new plan is generated, saved preferences are appended to the retrieval query as explicit preference text; hard safety constraints remain separate and authoritative. Chat references the active plan only when its run status is `COMPLETED`.

## Phase 4 frontend contract

- Chat is a message list rather than a single reply field. Submitting appends the user message immediately, exposes a sending state, and reports timeout or HTTP failure without discarding prior messages.
- Every assistant message renders `response_source` and its exact `memories_used`. An empty list is stated explicitly; the UI never claims that a memory was used unless the API returned it.
- A plan context is created only from a `COMPLETED` run containing selections and authoritative totals. The UI visibly distinguishes linked and unlinked chat states.
- The preference drawer loads with `GET`, replaces the complete explicit record with `PUT`, and removes the record with `DELETE`. Unsaved edits remain local until the user presses save.
- Memory categories are limited to the domain contract. Allergy and health information are not memory categories and must not be inferred from chat or behaviour.
