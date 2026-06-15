"""PREREQ-3 — server-side preflight prerequisite rows + scaffold-env endpoint.

TDD: these tests are written before the implementation and must fail against the
current code (no prereq rows, no ``launch_blocked``, no ``/api/setup/scaffold-env``).

The two foundation modules are complete and consumed read-only:
  - ``autodev.pipeline.prereq_spec.parse_prerequisites`` (PREREQ-1)
  - ``autodev.pipeline.host_probes.probe``                (PREREQ-2)

``host_probes.probe`` is patched directly (not via ``subprocess.run``) so each test
controls the found/missing/unknown outcome deterministically and offline.

Safety spine asserted explicitly: **no env value** ever appears in a preflight row
message, in the scaffold response, or in a file Lullabeast writes — Lullabeast only
writes blank ``KEY=`` scaffolding, append-only, never overwriting an existing line.
"""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ui.server import _run_preflight_checks


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirror tests/test_api_setup_preflight.py house style)
# ---------------------------------------------------------------------------

WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]

# A valid verification.md (passes _validate_verification_content) plus a
# ## Prerequisites block — two tools, two env keys (one config, one secret).
VERIFICATION_WITH_PREREQS = (
    "# Verification\n\n"
    "## Project type\n"
    "cli\n\n"
    "## Entry point\n"
    "- Command: `mycli --help`\n"
    "- Ready signal: process exits 0\n\n"
    "## Public surface\n"
    "1. Do the thing\n\n"
    "## Verification stack\n"
    "- Acceptance tool: subprocess + assertions\n\n"
    "## Prerequisites\n\n"
    "### Tools\n"
    "- node — Node.js 20+ runtime — needed by all\n"
    "- unity6 — Unity 6 LTS — needed by INFRA-1\n\n"
    "### Environment\n"
    "- API_BASE_URL (config) — base URL the app calls — used by all\n"
    "- OPENAI_API_KEY (secret) — provider key for the app — used by CORE-3\n"
)

# Same valid doc with NO ## Prerequisites block (baseline-identical case).
VERIFICATION_NO_PREREQS = (
    "# Verification\n\n"
    "## Project type\n"
    "cli\n\n"
    "## Entry point\n"
    "- Command: `mycli --help`\n"
    "- Ready signal: process exits 0\n\n"
    "## Public surface\n"
    "1. Do the thing\n\n"
    "## Verification stack\n"
    "- Acceptance tool: subprocess + assertions\n"
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
    """Minimal git subprocess stub so the non-prereq checks don't blow up."""
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
    """Build a full preflight-ready project: repo + openclaw symlink + git + roadmap.

    Returns (repo_path, config).
    """
    repo = tmp_path / "myproject"
    repo.mkdir()
    openclaw = _make_openclaw_dir(tmp_path, repo)
    (repo / ".git").mkdir()
    (repo / "roadmap.md").write_text("# Roadmap\n")
    (repo / ".gitignore").write_text(".autodev/pipeline/\n")
    if verification is not None:
        (repo / "verification.md").write_text(verification)
    return repo, _preflight_config(openclaw, repo)


def _fake_probe(*, missing=(), unknown=()):
    """Build a host_probes.probe stand-in keyed by capability name.

    Anything not in ``missing`` / ``unknown`` resolves to ``found`` + a version.
    """
    def _p(capability):
        cap = str(capability)
        if cap in missing:
            return {
                "status": "missing",
                "detail": f"'{cap}' not found on PATH",
                "guidance": f"Install {cap} and ensure it is on PATH",
            }
        if cap in unknown:
            return {"status": "unknown", "detail": f"'{cap} --version' timed out"}
        return {"status": "found", "version": "1.2.3", "detail": f"{cap}: 1.2.3"}
    return _p


def _run(repo, config, *, missing=(), unknown=()):
    """Run preflight with host_probes.probe patched and git subprocess stubbed."""
    with patch("autodev.pipeline.host_probes.probe",
               side_effect=_fake_probe(missing=missing, unknown=unknown)), \
         patch("subprocess.run", side_effect=_mock_git_subprocess):
        return _run_preflight_checks(str(repo), config=config)


def _row(rows, check_name):
    return next((r for r in rows if r.get("check") == check_name), None)


# ---------------------------------------------------------------------------
# Tool rows
# ---------------------------------------------------------------------------

class TestToolRows:
    def test_present_tool_yields_pass(self, tmp_path):
        repo, config = _make_project(tmp_path)
        rows = _run(repo, config)  # all found
        node = _row(rows, "tool: node")
        assert node is not None
        assert node["status"] == "pass"
        assert node["prereq"] == "tool"
        assert node["required"] is True

    def test_missing_required_tool_yields_fail_with_guidance(self, tmp_path):
        repo, config = _make_project(tmp_path)
        rows = _run(repo, config, missing=("unity6",))
        unity = _row(rows, "tool: unity6")
        assert unity is not None
        assert unity["status"] == "fail"
        assert unity["required"] is True
        # guidance must ride the row so the operator knows what to install
        assert unity.get("guidance")
        # a present tool in the same run stays pass
        assert _row(rows, "tool: node")["status"] == "pass"

    def test_unknown_probe_tool_yields_warn_not_fail(self, tmp_path):
        # The "advisory" / non-blocking path is the inconclusive probe outcome
        # (browser/timeout): unknown → warn, never fail (DEC-4).
        repo, config = _make_project(tmp_path)
        rows = _run(repo, config, unknown=("unity6",))
        unity = _row(rows, "tool: unity6")
        assert unity is not None
        assert unity["status"] == "warn"


# ---------------------------------------------------------------------------
# Env rows — presence only, three states, never a value
# ---------------------------------------------------------------------------

class TestEnvRows:
    def test_env_absent_warns_not_yet(self, tmp_path):
        repo, config = _make_project(tmp_path)  # no .env written
        rows = _run(repo, config)
        row = _row(rows, "env: API_BASE_URL")
        assert row is not None
        assert row["status"] == "warn"
        assert row["prereq"] == "env"
        assert row["kind"] == "config"
        assert "not yet" in row["message"].lower()

    def test_env_present_but_empty_warns(self, tmp_path):
        repo, config = _make_project(tmp_path)
        (repo / ".env").write_text("API_BASE_URL=\n")
        rows = _run(repo, config)
        row = _row(rows, "env: API_BASE_URL")
        assert row["status"] == "warn"
        assert "empty" in row["message"].lower()

    def test_env_present_with_value_passes(self, tmp_path):
        repo, config = _make_project(tmp_path)
        (repo / ".env").write_text("API_BASE_URL=https://example.test\n")
        rows = _run(repo, config)
        row = _row(rows, "env: API_BASE_URL")
        assert row["status"] == "pass"

    def test_secret_env_row_carries_kind(self, tmp_path):
        repo, config = _make_project(tmp_path)
        rows = _run(repo, config)
        row = _row(rows, "env: OPENAI_API_KEY")
        assert row["kind"] == "secret"
        assert row["required"] is False  # env keys never block launch


# ---------------------------------------------------------------------------
# Security spine — no value ever appears in a row message
# ---------------------------------------------------------------------------

class TestNoValueLeak:
    def test_no_row_message_contains_a_value(self, tmp_path):
        repo, config = _make_project(tmp_path)
        (repo / ".env").write_text(
            "API_BASE_URL=https://secret.internal\n"
            "OPENAI_API_KEY=sk-supersecret-123\n"
        )
        rows = _run(repo, config)
        blob = json.dumps(rows)
        assert "sk-supersecret-123" not in blob
        assert "secret.internal" not in blob
        # but the keys themselves still resolve to pass (value present)
        assert _row(rows, "env: OPENAI_API_KEY")["status"] == "pass"


# ---------------------------------------------------------------------------
# Additive guarantee — no block ⇒ baseline-identical (zero new rows)
# ---------------------------------------------------------------------------

class TestAdditive:
    def test_no_prerequisites_block_is_baseline_identical(self, tmp_path):
        repo, config = _make_project(tmp_path, verification=VERIFICATION_NO_PREREQS)
        rows = _run(repo, config)
        prereq_rows = [r for r in rows
                       if str(r.get("check", "")).startswith(("tool:", "env:"))]
        assert prereq_rows == []
        # the verification doc itself still validates
        assert _row(rows, "verification doc")["status"] == "pass"

    def test_missing_verification_md_emits_no_prereq_rows(self, tmp_path):
        repo, config = _make_project(tmp_path, verification=None)
        rows = _run(repo, config)
        prereq_rows = [r for r in rows
                       if str(r.get("check", "")).startswith(("tool:", "env:"))]
        assert prereq_rows == []


# ---------------------------------------------------------------------------
# Endpoint launch_blocked — required-tool fails block, env/unknown don't
# ---------------------------------------------------------------------------

def _client():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestLaunchBlocked:
    def test_launch_blocked_true_on_missing_required_tool(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        client = _client()
        with patch("autodev.pipeline.host_probes.probe",
                   side_effect=_fake_probe(missing=("unity6",))), \
             patch("subprocess.run", side_effect=_mock_git_subprocess):
            resp = client.post("/api/setup/preflight", json={"repo_path": str(repo)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["launch_blocked"] is True

    def test_launch_not_blocked_when_tools_present(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        client = _client()
        with patch("autodev.pipeline.host_probes.probe",
                   side_effect=_fake_probe()), \
             patch("subprocess.run", side_effect=_mock_git_subprocess):
            resp = client.post("/api/setup/preflight", json={"repo_path": str(repo)})
        body = resp.json()
        assert body["launch_blocked"] is False

    def test_launch_not_blocked_by_unknown_tool_or_env(self, tmp_path):
        repo, _ = _make_project(tmp_path)  # no .env ⇒ env warns, must not block
        client = _client()
        with patch("autodev.pipeline.host_probes.probe",
                   side_effect=_fake_probe(unknown=("unity6",))), \
             patch("subprocess.run", side_effect=_mock_git_subprocess):
            resp = client.post("/api/setup/preflight", json={"repo_path": str(repo)})
        body = resp.json()
        assert body["launch_blocked"] is False


# ---------------------------------------------------------------------------
# POST /api/setup/scaffold-env — value-free, append-only, atomic
# ---------------------------------------------------------------------------

class TestScaffoldEnv:
    def test_creates_env_with_blank_keys_and_comments(self, tmp_path):
        repo, _ = _make_project(tmp_path)  # no .env yet
        client = _client()
        resp = client.post("/api/setup/scaffold-env", json={"repo_path": str(repo)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        env_text = (repo / ".env").read_text()
        # blank KEY= lines for each declared key
        assert "API_BASE_URL=\n" in env_text or env_text.rstrip().endswith("API_BASE_URL=")
        assert "OPENAI_API_KEY=" in env_text
        # purpose comment precedes a key
        assert "# base URL the app calls" in env_text
        # both keys reported and now present-but-empty
        assert set(body["written"]) == {"API_BASE_URL", "OPENAI_API_KEY"}
        assert body["env"]["API_BASE_URL"] == "empty"
        assert body["env"]["OPENAI_API_KEY"] == "empty"

    def test_appends_only_absent_keys(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        (repo / ".env").write_text("API_BASE_URL=https://already.set\n")
        client = _client()
        resp = client.post("/api/setup/scaffold-env", json={"repo_path": str(repo)})
        body = resp.json()
        env_text = (repo / ".env").read_text()
        # the populated line is preserved verbatim
        assert "API_BASE_URL=https://already.set\n" in env_text
        # API_BASE_URL not re-appended (appears exactly once)
        assert env_text.count("API_BASE_URL=") == 1
        # only the absent key was written
        assert body["written"] == ["OPENAI_API_KEY"]

    def test_never_overwrites_populated_line_and_leaks_no_value(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        (repo / ".env").write_text("OPENAI_API_KEY=sk-real-secret-xyz\n")
        client = _client()
        resp = client.post("/api/setup/scaffold-env", json={"repo_path": str(repo)})
        body = resp.json()
        env_text = (repo / ".env").read_text()
        assert "OPENAI_API_KEY=sk-real-secret-xyz" in env_text  # untouched
        assert env_text.count("OPENAI_API_KEY=") == 1            # not duplicated
        assert "sk-real-secret-xyz" not in json.dumps(body)      # value never returned
        assert body["env"]["OPENAI_API_KEY"] == "set"

    def test_appended_keys_are_blank(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        client = _client()
        client.post("/api/setup/scaffold-env", json={"repo_path": str(repo)})
        for line in (repo / ".env").read_text().splitlines():
            if line.startswith("API_BASE_URL") or line.startswith("OPENAI_API_KEY"):
                assert line.endswith("=")  # nothing after the '='

    def test_ensures_env_is_gitignored(self, tmp_path):
        repo, _ = _make_project(tmp_path)
        client = _client()
        client.post("/api/setup/scaffold-env", json={"repo_path": str(repo)})
        gi = (repo / ".gitignore").read_text()
        assert ".env" in gi.splitlines()

    def test_no_declared_env_is_noop(self, tmp_path):
        # verification.md with a Prerequisites block but no Environment subsection
        ver = VERIFICATION_NO_PREREQS + (
            "\n## Prerequisites\n\n### Tools\n- node — runtime — needed by all\n"
        )
        repo, _ = _make_project(tmp_path, verification=ver)
        client = _client()
        resp = client.post("/api/setup/scaffold-env", json={"repo_path": str(repo)})
        body = resp.json()
        assert body["ok"] is True
        assert body["env"] == {}
        assert body["written"] == []
        assert not (repo / ".env").exists()

    def test_bad_repo_path_is_422(self, tmp_path):
        client = _client()
        resp = client.post("/api/setup/scaffold-env",
                           json={"repo_path": str(tmp_path / "does-not-exist")})
        assert resp.status_code == 422
