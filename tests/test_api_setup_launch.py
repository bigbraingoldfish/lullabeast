"""Tests for POST /api/setup/launch endpoint and _run_init_project helper."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_ROADMAP_SEED = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
)

PIPELINE_GITIGNORE_ENTRIES = [".autodev/pipeline/"]


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


from ui.server import _run_init_project


def _art(repo: Path) -> Path:
    return repo / ".autodev" / "pipeline"


def _make_subprocess_pass():
    """Return a side_effect function simulating all git commands succeeding."""
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = b""
        return mock
    return _inner


def _make_subprocess_status_dirty():
    """Return a side_effect function where git status --porcelain returns changes."""
    def _inner(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stderr = b""
        if isinstance(cmd, list) and "status" in cmd and "--porcelain" in cmd:
            mock.stdout = "A  roadmap.md\n"
        else:
            mock.stdout = ""
        return mock
    return _inner


def _symlink_patches():
    """Return context manager patches for symlink ops in ui.server module."""
    return (
        patch("ui.server.os.symlink"),
        patch("ui.server.os.path.lexists", return_value=False),
        patch("ui.server.os.remove"),
    )


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

class TestEndpointBasics:

    def _launch_orchestrator_patches(self, tmp_path):
        state_file = tmp_path / "launch_pipeline_state.json"
        orch_dir = tmp_path / "launch_orch"
        orch_dir.mkdir()
        (orch_dir / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "launch_pipeline.lock"),
            "autodev_repo_path": str(orch_dir),
        }
        return (
            patch("ui.server.load_config", return_value=cfg),
            patch("ui.server._check_orchestrator_liveness", return_value=False),
            patch("ui.server._spawn_orchestrator", return_value={"ok": True, "error": None}),
        )

    def test_endpoint_returns_200(self, tmp_path):
        """POST with valid repo_path and roadmap_seed (Mode A) returns 200 with ok=True."""
        repo_path = tmp_path / "myproject"
        client = load_server()
        p1, p2, p3 = self._launch_orchestrator_patches(tmp_path)

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"), \
             p1, p2, p3:
            response = client.post(
                "/api/setup/launch",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_ROADMAP_SEED},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_endpoint_missing_repo_path_returns_422(self):
        """POST without repo_path key returns 422."""
        client = load_server()
        response = client.post(
            "/api/setup/launch",
            json={"roadmap_seed": VALID_ROADMAP_SEED},
        )
        assert response.status_code == 422

    def test_endpoint_empty_repo_path_returns_422(self):
        """POST with empty string repo_path returns 422."""
        client = load_server()
        response = client.post(
            "/api/setup/launch",
            json={"repo_path": "", "roadmap_seed": VALID_ROADMAP_SEED},
        )
        assert response.status_code == 422

    def test_endpoint_response_schema(self, tmp_path):
        """Response body always has 'ok' and 'error' fields."""
        repo_path = tmp_path / "myproject"
        client = load_server()
        p1, p2, p3 = self._launch_orchestrator_patches(tmp_path)

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"), \
             p1, p2, p3:
            response = client.post(
                "/api/setup/launch",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_ROADMAP_SEED},
            )

        assert response.status_code == 200
        data = response.json()
        assert "ok" in data
        assert "error" in data


# ---------------------------------------------------------------------------
# Mode A unit tests (_run_init_project — new repo, no .git)
# ---------------------------------------------------------------------------

class TestModeADirectoryStructure:

    def test_mode_a_creates_directory_structure(self, tmp_path):
        """New dir gets .autodev/pipeline/phases/, tests/, src/{name}/, src/{name}/__init__.py."""
        repo_path = tmp_path / "myproject"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        assert (_art(repo_path) / "phases").is_dir()
        assert (repo_path / "tests").is_dir()
        assert (repo_path / "src" / "myproject").is_dir()
        assert (repo_path / "src" / "myproject" / "__init__.py").exists()

    def test_mode_a_creates_pipeline_json(self, tmp_path):
        """New dir gets pipeline.json with correct fields."""
        import json
        repo_path = tmp_path / "myproject"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        pipeline = json.loads((_art(repo_path) / "pipeline.json").read_text())
        assert pipeline["project"] == "myproject"
        assert pipeline["status"] == "idle"
        assert "created" in pipeline

    def test_mode_a_creates_all_expected_files(self, tmp_path):
        """Mode A creates pipeline.json, roadmap.md, prd.md, lessons.md, metrics.jsonl, .gitignore."""
        repo_path = tmp_path / "myproject"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        for fname in ["roadmap.md", "prd.md", ".gitignore"]:
            assert (repo_path / fname).exists(), f"Missing file: {fname}"
        for fname in ["pipeline.json", "lessons.md", "metrics.jsonl"]:
            assert (_art(repo_path) / fname).exists(), f"Missing artifact: {fname}"

    def test_mode_a_gitignore_has_pipeline_entries(self, tmp_path):
        """Mode A .gitignore contains the pipeline artifact directory entry."""
        repo_path = tmp_path / "myproject"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        gitignore = (repo_path / ".gitignore").read_text()
        for entry in PIPELINE_GITIGNORE_ENTRIES:
            assert entry in gitignore, f"Missing gitignore entry: {entry}"

    def test_mode_a_roadmap_md_contains_seed(self, tmp_path):
        """Mode A roadmap.md content equals roadmap_seed."""
        repo_path = tmp_path / "myproject"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        assert (repo_path / "roadmap.md").read_text() == VALID_ROADMAP_SEED

    def test_mode_a_invalid_roadmap_returns_error(self, tmp_path):
        """roadmap_seed with a phase line missing '> Test:' returns ok=False with error."""
        repo_path = tmp_path / "myproject"
        # Has a valid phase line format but no '> Test:' within 10 lines → validation fails
        bad_seed = "- [ ] `TEST-E1` | LOW | Do the thing\nNo test line follows.\n"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), bad_seed)

        assert result["ok"] is False
        assert result["error"] is not None
        assert len(result["error"]) > 0

    def test_mode_a_invalid_roadmap_cleans_up_dir(self, tmp_path):
        """On invalid roadmap, Mode A removes the created directory."""
        repo_path = tmp_path / "myproject"
        # Phase line with no '> Test:' triggers validation failure
        bad_seed = "- [ ] `TEST-E1` | LOW | Do the thing\nNo test line follows.\n"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), bad_seed)

        assert result["ok"] is False
        # Directory should have been cleaned up via shutil.rmtree
        assert not repo_path.exists()


# ---------------------------------------------------------------------------
# Mode A git failure tests
# ---------------------------------------------------------------------------

class TestModeAGitFailure:

    def test_mode_a_git_failure_returns_error(self, tmp_path):
        """patch subprocess.run to raise CalledProcessError → returns ok=False with error."""
        repo_path = tmp_path / "myproject"

        def raise_git_error(cmd, **kwargs):
            exc = subprocess.CalledProcessError(1, cmd)
            exc.stderr = b"fatal: not a git repository"
            raise exc

        with patch("subprocess.run", side_effect=raise_git_error), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is False
        assert result["error"] is not None
        assert len(result["error"]) > 0

    def test_mode_a_git_failure_cleans_up_dir(self, tmp_path):
        """On git CalledProcessError in Mode A, created directory is removed."""
        repo_path = tmp_path / "myproject"

        def fail_on_git(cmd, **kwargs):
            exc = subprocess.CalledProcessError(128, cmd)
            exc.stderr = b"error"
            raise exc

        with patch("subprocess.run", side_effect=fail_on_git), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is False
        # Directory should have been removed via shutil.rmtree
        assert not repo_path.exists()


# ---------------------------------------------------------------------------
# Mode B unit tests (_run_init_project — existing .git)
# ---------------------------------------------------------------------------

class TestModeBExistingRepo:

    def _make_git_repo(self, path: Path):
        """Create a real git repo with an initial commit."""
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
        # Try to switch to main branch (may already be main or may fail on older git)
        subprocess.run(["git", "-C", str(path), "checkout", "-b", "main"],
                       capture_output=True)
        (path / "README.md").write_text("# Test\n")
        env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
               "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}
        subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(path), "commit", "-m", "initial"],
            check=True, capture_output=True, env=env,
        )

    def test_mode_b_does_not_overwrite_existing_files(self, tmp_path):
        """Existing roadmap.md is preserved when Mode B runs."""
        repo_path = tmp_path / "myproject"
        self._make_git_repo(repo_path)
        original_content = "# My existing roadmap\n\n- [ ] `ORIG-E1` | HIGH | Original phase\n  > Test: Done.\n"
        (repo_path / "roadmap.md").write_text(original_content)

        with patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        # Existing roadmap.md should not be overwritten
        assert (repo_path / "roadmap.md").read_text() == original_content

    def test_mode_b_appends_missing_gitignore_entries(self, tmp_path):
        """Mode B .gitignore missing pipeline entries gets them appended."""
        repo_path = tmp_path / "myproject"
        self._make_git_repo(repo_path)
        # Write a .gitignore with only some entries present
        (repo_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")

        with patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        gitignore = (repo_path / ".gitignore").read_text()
        for entry in PIPELINE_GITIGNORE_ENTRIES:
            assert entry in gitignore, f"Missing gitignore entry after Mode B: {entry}"

    def test_mode_b_does_not_duplicate_gitignore_entries(self, tmp_path):
        """Mode B does not duplicate pipeline entries that already exist in .gitignore."""
        repo_path = tmp_path / "myproject"
        self._make_git_repo(repo_path)
        # Write a .gitignore with all entries already present
        existing = "\n".join(PIPELINE_GITIGNORE_ENTRIES) + "\n"
        (repo_path / ".gitignore").write_text(existing)

        with patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        gitignore = (repo_path / ".gitignore").read_text()
        assert gitignore.count(".autodev/pipeline/") == 1

    def test_mode_b_creates_missing_structure(self, tmp_path):
        """Mode B creates phases/, tests/, src/{name}/ if not present."""
        repo_path = tmp_path / "myproject"
        self._make_git_repo(repo_path)

        with patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        assert (_art(repo_path) / "phases").is_dir()
        assert (repo_path / "tests").is_dir()
        assert (repo_path / "src" / "myproject").is_dir()

    def test_mode_b_writes_prd_from_handoff(self, tmp_path):
        """Optional prd_content overwrites missing prd.md with Ideas handoff text."""
        repo_path = tmp_path / "myproject"
        self._make_git_repo(repo_path)
        prd = "# Product requirements\n\n## Problem\nFrom Ideas.\n"

        with patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED, prd)

        assert result["ok"] is True
        assert (repo_path / "prd.md").read_text() == prd


# ---------------------------------------------------------------------------
# Symlink tests
# ---------------------------------------------------------------------------

class TestSymlinkSetting:

    def test_sets_symlink_to_repo_path(self, tmp_path):
        """After success, os.symlink is called with repo_path as src."""
        repo_path = tmp_path / "myproject"
        symlink_calls = []

        def capture_symlink(src, dst):
            symlink_calls.append((src, dst))

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink", side_effect=capture_symlink), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        assert len(symlink_calls) == 1
        src, dst = symlink_calls[0]
        # src should be the expanded repo_path
        assert src == str(repo_path)
        # dst should be the configured project_dir_path (repo-local .autodev/ by default,
        # or ~/.openclaw/pipeline-project in legacy mode). Derive from load_config() so
        # the assertion survives future runtime layout changes.
        from ui.server import load_config
        cfg = load_config()
        expected_dst = os.path.expanduser(cfg["project_dir_path"])
        assert dst == expected_dst

    def test_removes_existing_symlink_before_creating(self, tmp_path):
        """When lexists returns True, os.remove is called before os.symlink."""
        repo_path = tmp_path / "myproject"
        call_order = []

        def track_remove(path):
            call_order.append(("remove", path))

        def track_symlink(src, dst):
            call_order.append(("symlink", src, dst))

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink", side_effect=track_symlink), \
             patch("ui.server.os.path.lexists", return_value=True), \
             patch("ui.server.os.remove", side_effect=track_remove):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result["ok"] is True
        assert call_order[0][0] == "remove"
        assert call_order[1][0] == "symlink"

    def test_symlink_not_set_on_failure(self, tmp_path):
        """On roadmap validation failure (Mode A), os.symlink is never called."""
        repo_path = tmp_path / "myproject"
        # Phase line without '> Test:' triggers validation failure
        bad_seed = "- [ ] `TEST-E1` | LOW | Do the thing\nNo test line follows.\n"
        symlink_calls = []

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink", side_effect=lambda s, d: symlink_calls.append((s, d))), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), bad_seed)

        assert result["ok"] is False
        assert len(symlink_calls) == 0


# ---------------------------------------------------------------------------
# Return value structure tests
# ---------------------------------------------------------------------------

class TestReturnValues:

    def test_success_returns_ok_true_error_none(self, tmp_path):
        """Successful run returns {"ok": True, "error": None}."""
        repo_path = tmp_path / "myproject"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), VALID_ROADMAP_SEED)

        assert result == {"ok": True, "error": None}

    def test_failure_returns_ok_false_error_string(self, tmp_path):
        """Failed run returns {"ok": False, "error": <non-empty string>}."""
        repo_path = tmp_path / "myproject"
        # Phase line without '> Test:' triggers validation failure
        bad_seed = "- [ ] `TEST-E1` | LOW | Do the thing\nNo test line follows.\n"

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"):
            result = _run_init_project(str(repo_path), bad_seed)

        assert result["ok"] is False
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0


class TestLaunchSpawnsOrchestrator:

    def test_launch_calls_spawn_after_init(self, tmp_path):
        """Successful launch writes pipeline_state and invokes _spawn_orchestrator."""
        import json

        repo_path = tmp_path / "myproject"
        state_file = tmp_path / "pipeline_state.json"
        orch_dir = tmp_path / "openclaw"
        orch_dir.mkdir()
        (orch_dir / "orchestrator.py").write_text("# mock orchestrator\n", encoding="utf-8")

        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(orch_dir),
        }
        client = load_server()

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=False), \
             patch("ui.server._spawn_orchestrator") as spawn_m:
            response = client.post(
                "/api/setup/launch",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_ROADMAP_SEED},
            )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        spawn_m.assert_called_once()
        called_path = spawn_m.call_args[0][0]
        assert os.path.samefile(called_path, str(repo_path.resolve()))
        assert state_file.exists()
        written = json.loads(state_file.read_text(encoding="utf-8"))
        assert written.get("pipeline_status") == "RUNNING"
        assert written.get("project_path") == str(repo_path.resolve())

    def test_launch_409_when_orchestrator_lock_held(self, tmp_path):
        repo_path = tmp_path / "myproject"
        state_file = tmp_path / "pipeline_state.json"
        orch_dir = tmp_path / "openclaw"
        orch_dir.mkdir()
        (orch_dir / "orchestrator.py").write_text("# mock\n", encoding="utf-8")
        cfg = {
            "pipeline_state_path": str(state_file),
            "lock_path": str(tmp_path / "pipeline.lock"),
            "autodev_repo_path": str(orch_dir),
        }
        client = load_server()

        with patch("subprocess.run", side_effect=_make_subprocess_pass()), \
             patch("ui.server.os.symlink"), \
             patch("ui.server.os.path.lexists", return_value=False), \
             patch("ui.server.os.remove"), \
             patch("ui.server.load_config", return_value=cfg), \
             patch("ui.server._check_orchestrator_liveness", return_value=True), \
             patch("ui.server._spawn_orchestrator") as spawn_m:
            response = client.post(
                "/api/setup/launch",
                json={"repo_path": str(repo_path), "roadmap_seed": VALID_ROADMAP_SEED},
            )

        assert response.status_code == 409
        data = response.json()
        assert data.get("code") == "orchestrator_running"
        assert data.get("ok") is False
        spawn_m.assert_not_called()
