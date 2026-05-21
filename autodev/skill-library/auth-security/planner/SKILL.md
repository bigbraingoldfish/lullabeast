---
name: auth-security-planner
description: Domain guidance for planning authentication and security phases. Loaded when phase category is AUTH.
---

# Auth/Security Planning Guidance

Make security properties explicit and verifiable. Do not assume the executor knows secure defaults — name them.

## Decomposition checklist
- Threat boundaries: client ↔ server, service ↔ service, tenant boundary.
- Auth model: exactly one of session-based (cookies), stateless tokens (JWT), or OAuth/OIDC.
- Single enforcement point (middleware/gateway/service guard) — no per-endpoint ad hoc checks.
- Authentication, session/token lifecycle, authorization, secrets handling as separate sub-areas.

## Interfaces & contracts to specify
- **Authentication:** password storage algorithm (Argon2id/bcrypt/scrypt), rate-limit + lockout policy, timing-safe credential compare, uniform error messages (no user enumeration).
- **Session/token lifecycle:** rotate on login and privilege change; idle + absolute timeout; server-side logout invalidation; for JWT pin required claims (`exp`/`iss`/`aud`), the signing algorithm, refresh rotation, and a revocation strategy.
- **Authorization:** RBAC/ABAC model with role list and per-action permissions; tenant isolation and IDOR protections; deny-by-default with allowed actions enumerated.
- **Secrets/defaults:** secrets from env/config only — never hardcoded, in repo, in logs, or in errors. HTTPS enforcement, baseline security headers, CORS constraints (no wildcards on sensitive endpoints).

## Edge cases — must enumerate, not generalise
Invalid credentials, expired tokens, unauthorised roles, cross-tenant probes, replay of refresh tokens, concurrent logout from another device, missing/malformed Authorization header.

## Pass criteria patterns
- Negative-path tests assert specific status code and that no sensitive data leaks in the response.
- Full functional test suite re-runs cleanly after the security change.
- A grep proves no secret literal lives in the source tree.

## Anti-patterns to avoid
- Logging credentials or tokens at any level (including DEBUG).
- Storage choice left unspecified (cookie flags / keychain / env / vault must be pinned), and likewise expiry and rotation.
- Implementing roles per endpoint rather than via the central policy.

## TDD test structure
Minimum: one positive-path test per auth flow, one negative-path test per enumerated edge case above, one cross-tenant isolation test.
