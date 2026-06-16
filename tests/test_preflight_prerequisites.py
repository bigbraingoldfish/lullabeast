"""Server-side prerequisites: `.env.example` emission + `.env` gitignore hygiene.

Host-tool detection was **removed** — a reliable present/absent verdict from an
arbitrary declared tool name isn't achievable (`Python 3.10+` / `Unity 6 LTS` aren't
PATH binaries), so it produced false-positive Launch blocks. What remains:

  - Declared ``### Tools`` are **not** probed or gated (documentation only).
  - Declared ``### Environment`` NAMES are materialized into a committed ``.env.example``
    (`_emit_env_example`) — value-free, append-only.
  - The user's real ``.env`` (which they create from the example and fill with secrets)
    is **gitignored** so the orchestrator's per-phase ``git add .`` can never commit it,
    while ``.env.example`` stays trackable (`_ensure_env_gitignore_hygiene`).
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from ui.server import _run_preflight_checks, _emit_env_example, _ensure_env_gitignore_hygiene
from autodev.pipeline.prereq_spec import parse_prerequisites


WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]

VERIFICATION_WITH_PREREQS = (
    "# Verification\n\n"
    "## Project type\ncli\n\n"
    "## Entry point\n- Command: `mycli --help`\n- Ready signal: process exits 0\n\n"
    "## Public surface\n1. Do the thing\n\n"
    "## Verification stack\n- Acceptance tool: subprocess + assertions\n\n"
    "## Prerequisites\n\n"
    "### Tools\n"
    "- node — Node.js 20+ runtime — needed by all\n"
    "- unity6 — Unity 6 LTS — needed by INFRA-1\n\n"
    "### Environment\n"
    "- API_BASE_URL (config) — base URL the app calls — used by all\n"
    "- OPENAI_API_KEY (secret) — provider key for the app — used by CORE-3\n"
)

VERIFICATION_NO_PREREQS = (
    "# Verification\n\n"
    "## Project type\ncli\n\n"
    "## Entry point\n- Command: `mycli --help`\n- Ready signal: process exits 0\n\n"
    "## Public surface\n1. Do the thing\n\n"
    "## Verification stack\n- Acceptance tool: subprocess + assertions\n"
)


def _make_openclaw_dir(tmp_path: Path, repo_path: Path):
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    (openclaw / "pipeline-project").symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS:
        ws = openclaw / f"workspace-{agent}"
        ws.mkdir(parents=True, exist_ok=True)
        for doc in WORKSPACE_DOCS:
            (ws / doc).write_text(f"# {doc}\n")
    return openclaw


def _preflight_config(openclaw: Path, repo_path: Path) -> dict:
    pp = str(openclaw / "pipeline-project")
    oc = str(openclaw)
    return {
        "openclaw_root": oc,
        "project_dir_path": pp,
        "autodev_repo_path": str(repo_path),
        "autodev_pipeline_root": oc,
        "pipeline_state_path": os.path.join(oc, "pipeline_state.json"),
        "lock_path": os.path.join(oc, "pipeline.lock"),
        "pipeline_queue_path": os.path.join(oc, "pipeline_queue.json"),
        "events_path": os.path.join(oc, "pipeline_events.jsonl"),
        "ideas_dir": os.path.join(oc, "ideas"),
        "phase_state_path": os.path.join(pp, ".autodev", "pipeline", "phase_state.json"),
        "roadmap_path": os.path.join(pp, "roadmap.md"),
    }


def _mock_git_subprocess(cmd, **kwargs):
    mock = MagicMock()
    mock.returncode = 0
    mock.stderr = ""
    mock.stdout = ""
    if isinstance(cmd, list) and cmd and cmd[0] == "git" and "--version" in cmd:
        mock.stdout = "git version 2.40.0\n"
    elif isinstance(cmd, list) and "branch" in cmd and "--list" in cmd:
        mock.stdout = "  main\n"
    elif isinstance(cmd, list) and "symbolic-ref" in cmd:
        mock.stdout = "main\n"
    return mock


def _make_project(tmp_path: Path, verification: str | None = VERIFICATION_WITH_PREREQS):
    repo = tmp_path / "myproject"
    repo.mkdir()
    openclaw = _make_openclaw_dir(tmp_path, repo)
    (repo / ".git").mkdir()
    (repo / "roadmap.md").write_text("# Roadmap\n")
    (repo / ".gitignore").write_text(".autodev/pipeline/\n")
    if verification is not None:
        (repo / "verification.md").write_text(verification)
    return repo, _preflight_config(openclaw, repo)


def _run(repo, config):
    with patch("subprocess.run", side_effect=_mock_git_subprocess):
        return _run_preflight_checks(str(repo), config=config)


def _row(rows, prefix):
    return [r for r in rows if str(r.get("check", "")).startswith(prefix)]


# ---------------------------------------------------------------------------
# Tool detection is gone — declared tools are never probed or gated
# ---------------------------------------------------------------------------

class TestNoToolDetection:
    def test_declared_tools_produce_no_check_rows(self, tmp_path):
        # Even with declared (and absent) host tools, preflight emits NO tool rows
        # and no fail — the false-positive block is gone for good.
        repo, config = _make_project(tmp_path)
        rows = _run(repo, config)
        assert _row(rows, "tool:") == []
        assert not any(r.get("status") == "fail" for r in rows)

    def test_no_env_check_rows_either(self, tmp_path):
        repo, config = _make_project(tmp_path)
        assert _row(_run(repo, config), "env:") == []

    def test_no_prerequisites_block_is_baseline(self, tmp_path):
        repo, config = _make_project(tmp_path, verification=VERIFICATION_NO_PREREQS)
        rows = _run(repo, config)
        assert _row(rows, "tool:") == [] and _row(rows, "env:") == []


# ---------------------------------------------------------------------------
# .env.example emission — committed, value-free, append-only
# ---------------------------------------------------------------------------

class TestEmitEnvExample:
    def test_preflight_emits_env_example(self, tmp_path):
        repo, config = _make_project(tmp_path)
        _run(repo, config)
        text = (repo / ".env.example").read_text()
        assert "API_BASE_URL=" in text and "OPENAI_API_KEY=" in text
        assert "# base URL the app calls" in text

    def test_keys_are_blank(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        written = _emit_env_example(str(repo), parse_prerequisites(VERIFICATION_WITH_PREREQS))
        assert set(written) == {"API_BASE_URL", "OPENAI_API_KEY"}
        for line in (repo / ".env.example").read_text().splitlines():
            if line.startswith(("API_BASE_URL", "OPENAI_API_KEY")):
                assert line.endswith("=")

    def test_append_only(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        (repo / ".env.example").write_text("API_BASE_URL=https://already.set\n")
        written = _emit_env_example(str(repo), parse_prerequisites(VERIFICATION_WITH_PREREQS))
        text = (repo / ".env.example").read_text()
        assert "API_BASE_URL=https://already.set\n" in text
        assert text.count("API_BASE_URL=") == 1
        assert written == ["OPENAI_API_KEY"]

    def test_no_declared_env_is_noop(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        assert _emit_env_example(str(repo), parse_prerequisites(VERIFICATION_NO_PREREQS)) == []
        assert not (repo / ".env.example").exists()


# ---------------------------------------------------------------------------
# .env gitignore hygiene — the security fix (#1) + keep .env.example trackable (#2)
# ---------------------------------------------------------------------------

def _gitignore_lines(repo):
    return [ln.strip() for ln in (repo / ".gitignore").read_text().splitlines()]


class TestEnvGitignoreHygiene:
    def test_emitting_example_gitignores_the_real_env(self, tmp_path):
        # #1 — the user's real .env (with secrets) MUST be ignored so the orchestrator's
        # per-phase `git add .` can never commit it.
        repo, config = _make_project(tmp_path)  # .gitignore has only .autodev/pipeline/
        _run(repo, config)
        assert ".env" in _gitignore_lines(repo)

    def test_no_env_declared_does_not_touch_gitignore(self, tmp_path):
        repo, config = _make_project(tmp_path, verification=VERIFICATION_NO_PREREQS)
        before = (repo / ".gitignore").read_text()
        _run(repo, config)
        assert (repo / ".gitignore").read_text() == before

    def test_existing_env_ignore_not_duplicated(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        (repo / ".gitignore").write_text(".autodev/pipeline/\n.env\n")
        _ensure_env_gitignore_hygiene(str(repo))
        assert _gitignore_lines(repo).count(".env") == 1

    def test_env_star_glob_keeps_example_trackable(self, tmp_path):
        # #2 — a common `.env*` glob would also ignore `.env.example`; we must add an
        # explicit un-ignore so the committed template still lands.
        repo, _ = _make_project(tmp_path)
        (repo / ".gitignore").write_text(".env*\n")
        _ensure_env_gitignore_hygiene(str(repo))
        lines = _gitignore_lines(repo)
        assert "!.env.example" in lines
        # the negation must come AFTER the .env* glob (last-match-wins) to actually win
        assert lines.index("!.env.example") > lines.index(".env*")

    def test_star_env_glob_needs_no_unignore(self, tmp_path):
        # `*.env` ignores `.env` (good) but NOT `.env.example` — so no un-ignore needed,
        # and .env is already covered so no duplicate `.env` line is added.
        repo, _ = _make_project(tmp_path)
        (repo / ".gitignore").write_text("*.env\n")
        _ensure_env_gitignore_hygiene(str(repo))
        lines = _gitignore_lines(repo)
        assert "!.env.example" not in lines
        assert ".env" not in lines  # already covered by *.env
