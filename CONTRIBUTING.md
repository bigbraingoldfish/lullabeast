# Contributing to Lullabeast

This project follows a [Code of Conduct](CODE_OF_CONDUCT.md) (the Contributor Covenant); by participating you agree to uphold it.

## Development setup

Development happens in the **development container** ([deploy/README.md, "Development container"](deploy/README.md#development-container)): the exact sandbox users run, with your working tree bind-mounted live, the UI server hot-reloading, and the test suites runnable in-container.

```bash
git clone <this-repo> autodev-ui
cd autodev-ui/deploy
docker compose -f docker-compose.dev.yml up -d
```

The boot log prints the dashboard URL (default `http://127.0.0.1:28790`, tokenized). Edits to `ui/` and `autodev/` are live; pipeline processes spawn fresh per run, and agent files, skills, and the plugin redeploy on a container restart.

## Running tests

Two suites; both must pass before any PR is merged. Run them inside the dev container (`requirements-dev.txt` is installed at boot):

```bash
docker compose -f docker-compose.dev.yml exec lullabeast pytest autodev/tests -q   # pipeline (no running OpenClaw needed)
docker compose -f docker-compose.dev.yml exec lullabeast pytest tests -q           # UI server + frontend
```

**Browser end-to-end tests** (`tests/test_browser_path_selector.py`) drive a real Chromium through Playwright's *Python* bindings against the live server. The dev container has all three prerequisites (the `playwright` package from `requirements-dev.txt`, the baked Chromium, the running server); authentication is a deliberate explicit opt-in via `AUTODEV_UI_E2E_TOKEN` (these tests add/delete queue rows and touch recents):

```bash
docker compose -f docker-compose.dev.yml exec lullabeast bash -c \
  'AUTODEV_UI_E2E_URL=http://127.0.0.1:$UI_PORT \
   AUTODEV_UI_E2E_TOKEN=$(cat /data/secrets/ui_token) \
   pytest tests/test_browser_path_selector.py -q'
```

A wrong token **fails** loudly; a token-protected server without the opt-in **skips** with a hint. If the package, the Chromium binary, or the server is absent, the module likewise **skips loudly** with an actionable reason instead of erroring — but a skip is not a pass.

**Path fixture rule:** `/home/pi/` paths are not acceptable in test fixtures. Use `tmp_path` (pytest's built-in fixture) for all temporary directories. Tests that hard-code machine-specific paths will be rejected.

## Environment variables

All environment variables consumed by the codebase are documented in [`.env.example`](.env.example) with inline comments. Any PR that introduces a new environment variable **must** add a corresponding commented entry to `.env.example` before the PR is merged. Do not add the variable only to code.

Keep secrets (tokens, API keys) in `.env` (gitignored). Never commit them in `ui/config.json` or any other tracked file.

## PR conventions

- **One concern per PR.** A bug fix and a feature change belong in separate PRs. Refactors belong in separate PRs from behaviour changes.
- **Reference related issues.** If the PR addresses a tracked issue or audit item, include the issue number or finding ID in the PR description.
- **No force-pushes to `main`.** Use a new commit to fix review feedback; do not rewrite published history.
- **Tests first.** The project was built TDD. If you add behaviour, add tests that cover it.

## Adding skills or disciplines

Skills live in `autodev/skill-library/{discipline}/{agent_role}/SKILL.md`. The mapping from roadmap subsystem prefixes to discipline directories is controlled by `autodev/config/skill_mapping.yaml`. Skill injection per phase is toggled at the agent level via the `pipeline.skills` flags in `openclaw.json`.

Before adding a new discipline mapping to `skill_mapping.yaml`, confirm the relationship between the roadmap subsystem prefix and the discipline is direct and unambiguous — the YAML file's header comment is explicit that incorrect skill context is worse than no skill context.

Per-file skill size is bounded by OpenClaw's context-injection limits, configured in `openclaw.json` (see the [OpenClaw documentation](https://docs.openclaw.ai) for current caps).

## Code style

No formal style guide. Match the surrounding code. `ui/server.py` and `autodev/pipeline/orchestrator.py` are intentionally single-file by design — do not split them into sub-modules without a deliberate architectural decision. See [CLAUDE.md](CLAUDE.md) for the reasoning.

## Maintainer notes

- **Pin runtime deps** in [requirements.txt](requirements.txt) / [ui/requirements.txt](ui/requirements.txt);
  run `pip-audit -r requirements.txt` periodically and upgrade pins after review.
- **`install.sh` is idempotent** (`set -euo pipefail`, atomic JSON patches) and safe to re-run after a
  `git pull`; it audits the OpenClaw `hooks` block, registers pipeline agents, and rebuilds the plugin.
- **Before a public release**, run a secret scanner over full git history (e.g.
  [gitleaks](https://github.com/gitleaks/gitleaks) / [trufflehog](https://github.com/trufflesecurity/trufflehog));
  rotate anything real that was ever committed and consider `git filter-repo`.
