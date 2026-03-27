---
name: api-service-planner
description: Domain guidance for planning API and service layer phases. Loaded when phase category is API.
---

# API/Service Layer Planning Guidance

## Contract-first, not code-first
For every endpoint specify: method + path, auth requirement, request shape (per location: path/query/body), response shape, error envelope, and status-code mapping.

## Lock a canonical error envelope
Define one error response structure (code, message, details, request_id) used everywhere. Treat it as part of the API contract.

## Pass criteria must cover ugly paths
- Success case + at least 5 negative cases per endpoint: invalid type, missing required, extra/unknown fields, boundary values, auth missing/invalid.
- Explicit assertions for: status codes, content-type, empty responses (204 vs 200), error envelope shape.
- Routing: include 404 (no route) and 405 (wrong method) expectations.

## Middleware order is part of the spec
Write middleware chain in exact intended order (e.g., request_id → auth → validation → handler → error handler). Define where request-scoped context is created and how it propagates.

## Scope for single-pass
- Prefer phases that deliver: (1) shared validation + error framework, then (2) 1-3 endpoints, then (3) contract tests + edge cases.
- Never "implement the whole API layer" in one phase; split by resource/domain.
