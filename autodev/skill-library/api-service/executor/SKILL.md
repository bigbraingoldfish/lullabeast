---
name: api-service-executor
description: Domain guidance for implementing API and service layer phases. Loaded when phase category is API.
---

# API/Service Layer Implementation Guidance

## Validation (non-negotiable)
- Validate path params, query params, and body separately using strict schema.
- Reject unknown fields by default (no silent acceptance).
- Normalize input types at the edge (parse ints/uuids/dates once, not deep in logic).
- Enforce size limits where relevant (payload, arrays, strings).

## Error handling
- One canonical error envelope everywhere — do not invent per-route formats.
- Never return 200 on failure. Map failures to 4xx/5xx.
- Use a global exception handler; no scattered try/catch.
- Do not leak stack traces or internal identifiers in client errors.

## Middleware
- Order must match plan (auth before handler; error handler last).
- Always propagate control (call next/await chain) and propagate errors.
- Preserve request-scoped context (request_id, auth principal).

## Response consistency
- Always set content-type for JSON responses.
- Standardize success envelopes; keep uniform.
- Handle empty responses correctly (204 + no body vs 200 + payload).

## Testing requirements
- Assert: status code, headers, body shape, error envelope.
- Include: wrong types, missing fields, extra fields, boundary sizes, auth failure.
- Add routing tests for 404/405 and route param extraction.

## External & paid API integration
- Implement paid/external API calls behind a seam (a client interface or injected dependency) that defaults to a mock, fake, or local stub.
- Never make a live paid call during the pipeline run. Exercise the integration against the stub and capture the request/response as behavioral evidence (a saved log or fixture file).
- Keep the stub schema faithful to the provider's documented contract (status codes, error envelope, idempotency) so the wiring you prove is the wiring the user runs against the live provider later.
