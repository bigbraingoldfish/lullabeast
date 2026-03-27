---
name: auth-security-reviewer
description: Domain guidance for reviewing authentication and security phases. Loaded when phase category is AUTH.
---

# Auth/Security Review Guidance

## Mindset
"Tests pass" is not sufficient. Auth must be judged against adversarial properties.

## Checklist
### Authentication
- Password storage uses Argon2id/bcrypt/scrypt. No plaintext or reversible encryption.
- Brute-force defenses present.
- No user enumeration via error messages.

### Sessions
- Cookie flags: Secure, HttpOnly, SameSite.
- Rotation on login and privilege changes.
- Idle + absolute timeouts implemented and tested.
- Logout invalidates server-side state.

### Tokens (JWT)
- Signature verification mandatory. Algorithm pinning explicit.
- Required claims validated (exp, iss, aud).
- No long-lived tokens without justification.

### Authorization
- Server-side checks on ALL sensitive endpoints (admin, exports, debug).
- Tenant/object ownership checks (IDOR-resistant).
- Deny-by-default; no permissive fallthroughs.

### Secrets — grep for:
"password", "secret", "apikey", "api_key", "token", "jwt", "privateKey", "BEGIN PRIVATE KEY"
Flag: hardcoded creds, secrets in config examples, secrets in logs/errors.

## Auth bypass testing
- Role confusion: call endpoints as lower-privilege user → assert 401/403.
- IDOR: change resource IDs across tenants → assert denial.
- Token replay: use token after logout/rotation → assert invalidation.
- Expiry: mock time forward → assert rejection.

## Attribution
- Plan: missing requirements (no timeouts, no lockout, no algorithm pinning).
- Impl: plan explicit but code violates it.

## False negatives in tests
- Only testing authorized success, never 401/403.
- Mocking auth middleware so real enforcement never executes.
- JWT tests that decode but never validate signature/claims.
