# AutoDev UI

Autonomous multi-agent software development pipeline UI and orchestration: a **FastAPI** dashboard plus pipeline code that drives OpenClaw (planner → executor → reviewer loop).

## Quick start

1. **Prerequisites:** Linux, Python 3.9+, git, and a separate **OpenClaw** installation with its gateway on `localhost:18789`.
2. **Install:** See [SETUP.md](SETUP.md) — run `./install.sh`, then `source .env`.
3. **UI config:** Copy [ui/config.example.json](ui/config.example.json) to `ui/config.json` (gitignored) or rely on `install.sh` to create it. Set **`AUTODEV_HOOKS_TOKEN`** if you do not want the webhook token in JSON.
4. **Run the server:**

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

- Pin runtime deps in [ui/requirements.txt](ui/requirements.txt). Periodically run **`pip-audit -r ui/requirements.txt`** (or your org’s equivalent) and upgrade pins after review.
- Before a public release, run a **secret scanner on full git history** (e.g. [gitleaks](https://github.com/gitleaks/gitleaks) or [trufflehog](https://github.com/trufflesecurity/trufflehog)). If anything real was ever committed, rotate credentials and consider `git filter-repo`.
- Full orientation for contributors: [CLAUDE.md](CLAUDE.md).

## License

[MIT](LICENSE).
