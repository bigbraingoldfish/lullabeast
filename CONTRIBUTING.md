# Contributing to Lullabeast

## Development setup

```bash
git clone <this-repo> autodev-ui
cd autodev-ui
cp .env.example .env          # fill in AUTODEV_REPO_PATH and optionally AUTODEV_HOOKS_TOKEN
source .env
pip install -r ui/requirements.txt
```

Start the server:

```bash
source .env
uvicorn ui.server:app --host 127.0.0.1 --port 18790
```

The UI is available at `http://127.0.0.1:18790`. For full pipeline functionality you also need a running OpenClaw instance on `localhost:18789`. The UI server can be started and tested independently.

## Running tests

There are two test suites. Both must pass before any PR is merged.

**Pipeline tests** (orchestration, sentinel, skill injection — no running OpenClaw required):

```bash
source .env
pytest autodev/tests/ -q
```

**UI server tests** (~50 pytest files):

```bash
source .env
pytest tests/ -q
```

If you run without `.env` loaded, set the path explicitly:

```bash
AUTODEV_REPO_PATH=$(pwd) pytest tests/ -q
```

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
