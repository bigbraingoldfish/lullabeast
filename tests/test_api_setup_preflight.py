"""Tests for POST /api/setup/preflight endpoint and _run_preflight_checks helper."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Real subprocess.run before tests patch subprocess.run (avoids recursion in delegate).
_REAL_SUBPROCESS_RUN = subprocess.run


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


from ui.server import _run_preflight_checks, _preflight_materialize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_ROADMAP_SEED = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
)

WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


def _make_workspace(base_dir: Path, agent: str):
    """Create a complete workspace-{agent} directory with all required docs."""
    ws = base_dir / f"workspace-{agent}"
    ws.mkdir(parents=True, exist_ok=True)
    for doc in WORKSPACE_DOCS:
        (ws / doc).write_text(f"# {doc}\n")
    return ws


def _make_openclaw_dir(tmp_path: Path, repo_path: Path):
    """Create a minimal ~/.openclaw-like dir with symlink and all workspace dirs."""
    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir(parents=True, exist_ok=True)
    # symlink pipeline-project → repo_path
    (openclaw / "pipeline-project").symlink_to(repo_path)
    for agent in WORKSPACE_AGENTS:
        _make_workspace(openclaw, agent)
    return openclaw


def _make_git_repo(repo_path: Path, branch: str = "main"):
    """Create a minimal git repo structure (no actual git init, just dirs)."""
    (repo_path / ".git").mkdir(parents=True, exist_ok=True)


def _make_gitignore(repo_path: Path, content: str = ""):
    (repo_path / ".gitignore").write_text(content)


def _all_gitignore_entries():
    return [
        "*.done",
        "phase_state.json",
        "planner_output.json",
        "executor_output.json",
        "reviewer_output.json",
        "escalation_output.json",
        "current_phase.json",
    ]


def _full_gitignore_content():
    return "\n".join(_all_gitignore_entries()) + "\n"


def _mock_subprocess_branch_pass(cmd, **kwargs):
    """Simulate subprocess.run for branch listing — returns output."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "  main\n"
    mock.stderr = ""
    return mock


def _mock_subprocess_preflight_pass():
    """Mock git subprocess calls used by preflight when no real git repo is needed."""
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.stderr = ""
        if not isinstance(cmd, list) or not cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        if cmd[0] == "git" and len(cmd) >= 2 and cmd[1] == "--version":
            mock.returncode = 0
            mock.stdout = "git version 2.40.0\n"
            return mock
        if "branch" in cmd and "--list" in cmd:
            mock.returncode = 0
            mock.stdout = "  main\n"
            return mock
        if "symbolic-ref" in cmd:
            mock.returncode = 0
            mock.stdout = "main\n"
            return mock
        if cmd[0] == "git" and "init" in cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        if cmd[0] == "git" and "-C" in cmd and "branch" in cmd:
            mock.returncode = 0
            mock.stdout = ""
            return mock
        mock.returncode = 0
        mock.stdout = ""
        return mock
    return _inner


def _delegate_git_subprocess_preflight(cmd, **kwargs):
    """Run real git for integration; fall back to mock for any other commands."""
    if isinstance(cmd, list) and cmd and cmd[0] == "git":
        return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
    return _mock_subprocess_preflight_pass()(cmd, **kwargs)


# ---------------------------------------------------------------------------
# Unit tests for _run_preflight_checks
# ---------------------------------------------------------------------------

class TestSymlinkCheck:

    def test_symlink_pass(self, tmp_path):
        """Symlink resolves to repo_path → check status 'pass'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        sym = next(c for c in results if c["check"] == "symlink")
        assert sym["status"] == "pass"

    def test_symlink_fail(self, tmp_path):
        """Symlink points elsewhere → auto-created → status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        wrong_path = tmp_path / "other"
        wrong_path.mkdir()
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()
        # Symlink points to wrong_path, not repo_path
        (openclaw / "pipeline-project").symlink_to(wrong_path)
        for agent in WORKSPACE_AGENTS:
            _make_workspace(openclaw, agent)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        sym = next(c for c in results if c["check"] == "symlink")
        assert sym["status"] == "fixed"
        assert str(repo_path) in sym["message"] or "Symlink" in sym["message"]

    def test_symlink_missing_returns_fail(self, tmp_path):
        """No symlink at all → created → status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()
        for agent in WORKSPACE_AGENTS:
            _make_workspace(openclaw, agent)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        sym = next(c for c in results if c["check"] == "symlink")
        assert sym["status"] == "fixed"


class TestGitignoreCheck:

    def test_gitignore_missing_returns_fail(self, tmp_path):
        """No .gitignore → created → status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_git_repo(repo_path)
        # No .gitignore

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        gi = next(c for c in results if c["check"] == ".gitignore")
        assert gi["status"] == "fixed"

    def test_gitignore_entries_injected(self, tmp_path):
        """When .gitignore is present but missing entries → entries auto-added, status 'fixed'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_git_repo(repo_path)
        # Only write one entry so others are missing
        _make_gitignore(repo_path, "*.done\n")
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        entries_check = next(c for c in results if c["check"] == ".gitignore entries")
        assert entries_check["status"] == "fixed"
        assert "Added" in entries_check["message"]

    def test_gitignore_entries_all_present(self, tmp_path):
        """When .gitignore has all required entries → message is 'All required entries present'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_git_repo(repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        entries_check = next(c for c in results if c["check"] == ".gitignore entries")
        assert entries_check["status"] == "pass"
        assert "All required entries present" in entries_check["message"]


class TestGitRepoCheck:

    def test_git_repo_missing_auto_inits(self, tmp_path):
        """No .git dir → preflight runs git init and reports 'git repo' fixed."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        # No .git dir — use real git for init/branch

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_delegate_git_subprocess_preflight):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "fixed"
        assert (repo_path / ".git").is_dir()

    def test_git_repo_no_main_or_master_fails(self, tmp_path):
        """A .git dir exists but branch listing returns empty → 'git repo' check 'fail'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def no_branch(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "--version":
                mock.returncode = 0
                mock.stdout = "git version 2.40\n"
                return mock
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""  # empty = no main/master
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd:
                mock.returncode = 1
                mock.stdout = ""
                return mock
            if isinstance(cmd, list) and "symbolic-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "phase/foo\n"
                return mock
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=no_branch):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "fail"
        assert "no commits" in git["message"].lower() or "main" in git["message"].lower()

    def test_git_repo_unborn_main_branch_warns_not_fails(self, tmp_path):
        """Unborn main/master branch should warn with initial commit guidance."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def unborn_main(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "--version":
                mock.returncode = 0
                mock.stdout = "git version 2.40\n"
                return mock
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""  # no listed main/master branch (unborn case)
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd:
                mock.returncode = 1
                mock.stdout = ""
                return mock
            if "symbolic-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "main\n"
                return mock
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=unborn_main):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "warn"
        assert "no commits yet" in git["message"].lower()
        assert "commit -m 'init'" in git["message"]

    def test_git_repo_phase_only_branch_warns_when_head_has_commits(self, tmp_path):
        """Repo with commits on phase/* (no main/master) → warn, not fail."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        def phase_only(cmd, **kwargs):
            mock = MagicMock()
            mock.stderr = ""
            if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "--version":
                mock.returncode = 0
                mock.stdout = "git version 2.40\n"
                return mock
            if "branch" in cmd and "--list" in cmd:
                mock.returncode = 0
                mock.stdout = ""
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd and "--verify" in cmd:
                mock.returncode = 0
                mock.stdout = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
                return mock
            if isinstance(cmd, list) and "rev-parse" in cmd and "--abbrev-ref" in cmd:
                mock.returncode = 0
                mock.stdout = "phase/foo\n"
                return mock
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=phase_only):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git repo")
        assert git["status"] == "warn"
        assert "phase branches" in git["message"].lower() or "main" in git["message"].lower()


class TestGitExecutableCheck:

    def test_git_version_passes(self, tmp_path):
        """git --version succeeds → 'git' check status 'pass'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        git = next(c for c in results if c["check"] == "git")
        assert git["status"] == "pass"


class TestRoadmapFileCheck:

    def test_roadmap_file_missing_returns_warn(self, tmp_path):
        """No *oadmap*.md file → 'roadmap file' check status 'warn'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        # No roadmap file

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        roadmap = next(c for c in results if c["check"] == "roadmap file")
        assert roadmap["status"] == "warn"

    def test_roadmap_file_present_returns_pass(self, tmp_path):
        """A file matching *oadmap*.md → 'roadmap file' check status 'pass'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        roadmap = next(c for c in results if c["check"] == "roadmap file")
        assert roadmap["status"] == "pass"


class TestAllChecksInResponse:

    def test_all_checks_in_response(self, tmp_path):
        """Response includes symlink, .gitignore, git cli, git repo, workspace, roadmap."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)
        (repo_path / "roadmap.md").write_text("# Roadmap\n")

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        check_names = [c["check"] for c in results]
        assert "symlink" in check_names
        assert ".gitignore" in check_names or ".gitignore entries" in check_names
        assert "git" in check_names
        assert "git repo" in check_names
        assert any("workspace" in n for n in check_names)
        assert "roadmap file" in check_names

    def test_each_check_has_required_fields(self, tmp_path):
        """Every check result has 'check', 'status', and 'message' fields."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        with patch("os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            results = _run_preflight_checks(str(repo_path))

        for item in results:
            assert "check" in item
            assert "status" in item
            assert "message" in item
            assert item["status"] in ("pass", "fail", "warn", "fixed")


# ---------------------------------------------------------------------------
# Integration tests for POST /api/setup/preflight endpoint
# ---------------------------------------------------------------------------

class TestPreflightEndpoint:

    def test_endpoint_returns_200(self, tmp_path):
        """POST with valid (patched) repo_path returns 200 with 'checks' array."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        assert response.status_code == 200
        data = response.json()
        assert "checks" in data
        assert isinstance(data["checks"], list)
        assert len(data["checks"]) > 0

    def test_endpoint_requires_repo_path(self):
        """POST with empty repo_path returns 422."""
        client = load_server()
        response = client.post("/api/setup/preflight", json={"repo_path": ""})
        assert response.status_code == 422

    def test_endpoint_missing_repo_path_key(self):
        """POST with no repo_path key returns 422."""
        client = load_server()
        response = client.post("/api/setup/preflight", json={})
        assert response.status_code == 422

    def test_endpoint_checks_have_valid_statuses(self, tmp_path):
        """All check status values in the response are 'pass', 'fail', or 'warn'."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        assert response.status_code == 200
        for check in response.json()["checks"]:
            assert check["status"] in ("pass", "fail", "warn", "fixed"), \
                f"Unexpected status '{check['status']}' for check '{check['check']}'"

    def test_endpoint_response_schema(self, tmp_path):
        """Response body conforms to {checks: [{check, status, message}]} schema."""
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()

        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post("/api/setup/preflight", json={"repo_path": str(repo_path)})

        data = response.json()
        assert "checks" in data
        for item in data["checks"]:
            assert set(item.keys()) >= {"check", "status", "message"}


class TestPreflightMaterialize:
    """Unit tests for _preflight_materialize (roadmap/prd writes + conflicts)."""

    def test_writes_roadmap_when_missing(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        checks = _preflight_materialize(str(repo), VALID_ROADMAP_SEED, None)
        assert any(c["check"] == "roadmap write" and c["status"] == "fixed" for c in checks)
        assert (repo / "roadmap.md").read_text().strip() == VALID_ROADMAP_SEED.strip()

    def test_conflict_when_disk_differs(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / "roadmap.md").write_text("# Other\n")
        checks = _preflight_materialize(str(repo), VALID_ROADMAP_SEED, None)
        assert any(c["check"] == "roadmap conflict" and c["status"] == "fail" for c in checks)

    def test_prd_conflict(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / "prd.md").write_text("# A\n")
        checks = _preflight_materialize(str(repo), None, "# B\n")
        assert any(c["check"] == "prd conflict" and c["status"] == "fail" for c in checks)


class TestPreflightEndpointWithSeed:
    """POST /api/setup/preflight with roadmap_seed."""

    def test_writes_roadmap_via_seed(self, tmp_path):
        repo_path = tmp_path / "myproject"
        repo_path.mkdir()
        openclaw = _make_openclaw_dir(tmp_path, repo_path)
        _make_gitignore(repo_path, _full_gitignore_content())
        _make_git_repo(repo_path)

        client = load_server()
        with patch("ui.server.os.path.expanduser", side_effect=lambda p: str(openclaw) if "openclaw" in p else str(repo_path)), \
             patch("subprocess.run", side_effect=_mock_subprocess_preflight_pass()):
            response = client.post(
                "/api/setup/preflight",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_ROADMAP_SEED},
            )

        assert response.status_code == 200
        assert (repo_path / "roadmap.md").read_text().strip() == VALID_ROADMAP_SEED.strip()
        names = [c["check"] for c in response.json()["checks"]]
        assert "roadmap seed" in names
