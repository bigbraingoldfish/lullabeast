# Security Policy

## Security model

The Lullabeast API has **no authentication**. Every route under `/api/*` is reachable by anyone who can connect to the server's TCP port.

The UI and API are designed to run on loopback (`127.0.0.1`, port **18790**) and must not be exposed to untrusted networks without a reverse proxy, TLS, and access control in front. See [SETUP.md — Security and network exposure](SETUP.md#security-and-network-exposure) for binding options.

The agent pipeline (planner → executor → reviewer) executes code on the host machine. The trust boundary is the **local user account**: anyone who can invoke the pipeline API can cause code to run under the account that owns the Lullabeast process. Treat this as operator tooling for a trusted machine, not a multi-tenant service.

## Reporting vulnerabilities

**Non-sensitive findings** (documentation gaps, missing headers, hardening suggestions): open a [GitHub issue](../../issues) with the title prefix `[SECURITY]`.

**Sensitive findings** (credential exposure, remote code execution, authentication bypass): use [GitHub's private vulnerability reporting](../../security/advisories/new) so the report is not publicly visible until a fix is prepared.

Do not send vulnerability reports by email. We do not publish a security contact address.

## Scope

**In scope:**

- The pipeline agents and their gate scripts (`autodev/pipeline/gate_scripts/`)
- The UI server (`ui/server.py`) and its API routes
- The install script (`install.sh`) and its handling of secrets and file permissions
- The environment variable contract (`.env`, `.env.example`, `AUTODEV_HOOKS_TOKEN`, `OPENCLAW_ROOT`, `AUTODEV_REPO_PATH`)

**Out of scope:**

- The underlying LLM models or their hosted APIs (OpenRouter, Anthropic, etc.)
- OpenClaw itself — it is a separate project and has its own security surface
- Issues in third-party Python dependencies that are not exploitable via Lullabeast's code paths (report those upstream)

## Known limitations

| Limitation | Detail |
|---|---|
| No API authentication | All `/api/*` routes require no credentials. Bind to `127.0.0.1` unless on a trusted LAN behind a firewall. |
| Loopback-only recommendation | LAN or internet exposure without a reverse proxy + TLS is not supported and not recommended. |
| No rate limiting | The API has no rate limiting on any route. An attacker who can reach the port can call endpoints without restriction. |
| `.env` stores secrets in plaintext | `install.sh` writes `AUTODEV_HOOKS_TOKEN` (the OpenClaw webhook Bearer token) to `.env` as plaintext. This is standard practice for local tooling but means the file should be treated as a credential: `chmod 600 .env`, do not commit it, do not check it into shared storage. |
| Agent pipeline runs as local user | Gate scripts and the orchestrator run with the same OS privileges as the process that starts them. There is no sandboxing below the user account. |
