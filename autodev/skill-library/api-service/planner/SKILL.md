---
name: api-service-planner
description: Domain guidance for planning API and service layer phases. Loaded when phase category is API.
---

# API/Service Layer Planning Guidance

## Decomposition checklist
- Shared validation + error framework (canonical envelope: `code`, `message`, `details`, `request_id`).
- 1–3 endpoints per phase, grouped by resource — never "the whole API layer" in one phase.
- Middleware chain in exact order (e.g. request_id → auth → validation → handler → error handler) and where request-scoped context is created.
- Contract tests + edge cases as a deliverable, not an afterthought.

## Interfaces & contracts to specify
For every endpoint: method + path, auth requirement, request shape per location (path/query/body), response shape, error envelope, status-code mapping. If event-driven: event name constants, payload schema, delivery semantics (ordering, at-least-once, idempotency).

## Edge cases — must enumerate, not generalise
At least 5 negative cases per endpoint: invalid type, missing required field, extra/unknown fields, boundary values, auth missing/invalid. Plus routing: 404 (no route) and 405 (wrong method). Plus empty-response semantics (204 vs 200).

## Pass criteria patterns
- Status-code assertions per endpoint (success and each negative case).
- Content-type assertion and error envelope shape assertion.
- Schema-shape assertion on responses (no `assert resp.json` without keys).
- Contract test runs against the real handler, not a hand-rolled fake.

## Anti-patterns to avoid
- "Return an appropriate error" — pin the status code and envelope shape.
- Per-endpoint ad hoc auth checks instead of a single enforcement point.
- Multiple error envelope shapes across endpoints.
- Endpoints that share path prefix but split auth requirements without explicit documentation.

## TDD test structure
Minimum: one contract test file per endpoint group, one middleware-order test, one error-envelope conformance test.

## External & paid API boundaries
- When a phase integrates a paid/external third-party API (payment, LLM provider, SMS, etc.), pin the boundary contract with the same rigor as an internal endpoint: request shape, response shape(s), error/status mapping, idempotency/retry semantics.
- Plan verification against a mock, fake, or recorded boundary. The live paid call is out of scope for the pipeline — it is the user's final smoke, post-build.
- Make the boundary swappable: plan a seam (client interface, injected client, or base-URL override) so the executor defaults to a stub and the user can later point it at the live provider.
