---
name: auth-security-executor
description: Domain guidance for implementing authentication and security phases. Loaded when phase category is AUTH.
---

# Auth/Security Implementation Guidance

## Passwords and credentials
- Use Argon2id/bcrypt/scrypt via vetted library. Never plaintext, never reversible encryption.
- Constant-time comparisons for secrets/tokens.

## Sessions (cookie-based)
- Flags: Secure + HttpOnly + SameSite (choose policy explicitly).
- Rotate session ID on login, privilege change, password change.
- Enforce idle + absolute timeouts.
- Invalidate server-side on logout (not only clearing cookies).

## Tokens (JWT-based)
- Use vetted JWT library.
- Validate: signature, expected algorithm (pin allowed; reject "none"), exp + iss + aud.
- Short-lived access tokens; refresh rotation if refresh tokens exist.

## Authorization
- All checks server-side. Centralize in policy helper/middleware/guard.
- Deny by default; explicitly allow actions.
- Enforce tenant boundaries and object ownership (IDOR-resistant).

## Secrets handling
- Load from env or secrets manager. Redact from logs and errors.
- No "example secrets" that look real. No placeholders that could ship.

## What you must NEVER do
- Hardcode secrets, API keys, passwords, JWT signing keys.
- Print credentials/tokens in logs, errors, or traces.
- Roll custom crypto or token formats.
- Add "TODO: secure later" scaffolding.

## Testing
- Happy + negative: wrong password, expired token, invalid signature, unauthorized role, cross-tenant IDOR.
- Explicit 401/403 assertions on boundary violations.
