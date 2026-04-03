# AutoDev UI

Autonomous multi-agent software development pipeline UI and orchestration: a **FastAPI** dashboard plus pipeline code that drives OpenClaw (planner → executor → reviewer loop).

## Quick start

1. **Prerequisites:** Linux, Python 3.9+, git, and a separate **OpenClaw** installation with its gateway on `localhost:18789`.
2. **Install:** See [SETUP.md](SETUP.md) — run `./install.sh`, then `source .env`.
3. **Runtime layout:** Pipeline state, lock, queue, and ideas default under **`<repo>/.autodev/`**; OpenClaw config and workspaces stay under `~/.openclaw`. Legacy layout: [docs/RUNTIME-MIGRATION.md](docs/RUNTIME-MIGRATION.md).
4. **UI config:** Copy [ui/config.example.json](ui/config.example.json) to `ui/config.json` (gitignored) or rely on `install.sh` to create it. Set **`AUTODEV_HOOKS_TOKEN`** if you do not want the webhook token in JSON.
5. **Run the server:**

   ```bash
   source .env
   uvicorn ui.server:app --host 127.0.0.1 --port 18790
   ```

## Security

- **`/api/*` has no authentication.** Bind to **`127.0.0.1`** unless you are on a trusted LAN and understand the risk. Do not expose the raw port to the internet without a reverse proxy, TLS, and access control. Details: [SETUP.md — Security and network exposure](SETUP.md#security-and-network-exposure).
- Environment variables for sensitive or machine-specific values: see [`.env.example`](.env.example).

## Tests

```bash
source .env
pytest autodev/tests/ -q
pytest tests/ -q
```

## Maintainer notes

- **`install.sh` / OpenClaw:** Step 9 creates `agents.list` if missing, registers pipeline agents (`planner`, `executor`, `reviewer`, `escalation`, `prd-creator`, `roadmap-converter`) and `hooks.allowedAgentIds`, and warns when `tools.profile` is not `coding` or `full`. See [SETUP.md — openclaw.json](SETUP.md#openclawjson-requirements) and [OpenClaw tools docs](https://docs.openclaw.ai/tools).
- Pin runtime deps in [ui/requirements.txt](ui/requirements.txt). Periodically run **`pip-audit -r ui/requirements.txt`** (or your org’s equivalent) and upgrade pins after review.
- Before a public release, run a **secret scanner on full git history** (e.g. [gitleaks](https://github.com/gitleaks/gitleaks) or [trufflehog](https://github.com/trufflesecurity/trufflehog)). If anything real was ever committed, rotate credentials and consider `git filter-repo`.
- Full orientation for contributors: [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE).
