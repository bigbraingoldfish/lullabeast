---
name: api-service-reviewer
description: Domain guidance for reviewing API and service layer phases. Loaded when phase category is API.
---

# API/Service Layer Review Guidance

## Contract verification
- Each endpoint: method/path, auth rules, request/response shapes, required fields, status codes.
- Error envelope consistency across all endpoints and all failure types.
- Content-type headers and empty response semantics (204 vs 200).

## Validation coverage
- Validation exists for body + query + path parameters.
- Unknown fields rejected (or explicitly allowed by spec).
- Boundary limits exist where risk is plausible.

## Middleware integrity
- Auth ordering makes bypass impossible.
- Async error propagation correct (no swallowed exceptions).
- Request-scoped context created once and used consistently.

## Contract drift
- Compare implementation against stated API spec/pass criteria.
- Missing required fields? Breaking renames? Changed status codes?
- At least one negative test per major validation/auth/error class.

## Attribution
- Plan: pass_criteria omitted contract assertions, middleware order unspecified, endpoint contract underspecified.
- Impl: plan clear but validation/error/middleware behavior doesn't match, endpoints vary in conventions, auth bypassable.
