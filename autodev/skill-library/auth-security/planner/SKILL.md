---
name: auth-security-planner
description: Domain guidance for planning authentication and security phases. Loaded when phase category is AUTH.
---

# Auth/Security Planning Guidance

## Goal
Make security properties explicit and verifiable. Do not assume the executor knows secure defaults.

## Mandatory planning artifacts
- Threat boundaries: client ↔ server, service ↔ service, tenant boundary.
- Auth model: exactly one of session-based (cookies), stateless tokens (JWT), or OAuth/OIDC.
- Enforcement point: where authn/authz is enforced (middleware, gateway, service guard). No per-endpoint ad hoc checks.

## Requirements to specify (never leave implicit)
### Authentication
- Password storage method (Argon2id/bcrypt/scrypt).
- Brute-force defenses: rate limit + lockout + audit events.
- Credential validation: timing-safe compare, uniform error messages (no user enumeration).

### Session/token lifecycle
- Rotation: rotate session ID on login and privilege change.
- Expiration: idle + absolute timeout.
- Logout: server-side invalidation (not only cookie deletion).
- JWT: required claims (exp/iss/aud), algorithm pinning, refresh rotation, revocation strategy.

### Authorization
- RBAC/ABAC model with role list and per-action permissions.
- Tenant isolation and IDOR protections.
- Deny-by-default; explicitly enumerate allowed actions.

### Secrets and defaults
- Secrets from env/config only. Forbid: hardcoded, in repo, in logs, in errors.
- HTTPS enforcement. Baseline security headers. CORS constraints (no wildcards on sensitive endpoints).

## Pass criteria must verify security, not just functionality
- Negative-path tests: invalid credentials, expired tokens, unauthorized roles, cross-tenant probes.
- Rerun full functional suite after security changes.
