"""Executor gate: vite content-hashed build artifact rotation must not trigger ERR_UNACCOUNTED_DELETION.

Tests cover the four distinct branches introduced by the _is_build_artifact_rotation helper:
  1. Rotation (old hash deleted, new hash present) → gate PASS + WARN on stderr
  2. Wipe (old hash deleted, nothing left in dir) → gate FAIL as before
  3. Source file deleted alongside a harmless rotation → gate FAIL for source only
  4. Non-hash-named dist file deleted → gate FAIL (pattern does not match)
"""

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_SCRIPTS_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline", "gate_scripts")
for _p in [GATE_SCRIPTS_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import utils as utils_module
import executor_gate as executor_gate_module


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def _base_executor_payload(**overrides) -> dict:
    payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": [],
        "files_deleted": [],
    }
    payload.update(overrides)
    return payload


def _make_repo_with_dist_files(tmp_path: Path, committed_files: list[str]) -> tuple[Path, Path, str]:
    """Create a git repo at tmp_path/pipeline-project, commit all committed_files, return
    (workspace_path, artifacts_path, phase_base_commit)."""
    root = tmp_path
    workspace = root / "pipeline-project"
    workspace.mkdir()

    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "pytest")

    for rel in committed_files:
        fpath = workspace / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(f"content of {rel}\n", encoding="utf-8")
        _git(workspace, "add", rel)

    _git(workspace, "commit", "-m", "init")
    phase_base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    art = workspace / ".autodev" / "pipeline"
    art.mkdir(parents=True)

    planner = {"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}
    (art / "planner_output.json").write_text(json.dumps(planner), encoding="utf-8")

    (root / "pipeline_state.json").write_text(
        json.dumps({"phase_base_commit": phase_base}),
        encoding="utf-8",
    )

    return workspace, art, phase_base


def _run_gate(workspace: Path, art: Path, executor_payload: dict, capsys=None) -> str:
    ws_str = str(workspace) + os.sep
    art_str = str(art) + os.sep
    ps_path = str(art / "phase_state.json")

    exec_path = art / "executor_output.json"
    exec_path.write_text(json.dumps(executor_payload), encoding="utf-8")

    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))

    with stack:
        return executor_gate_module.evaluate_executor(str(exec_path))


# ---------------------------------------------------------------------------
# Test 1: build artifact rotation → PASS with WARN
# ---------------------------------------------------------------------------

def test_vite_hash_rotation_passes_gate(tmp_path, capsys):
    """Old vite hash bundles deleted + new ones present → gate passes with a warning.

    This is the core regression: executor runs npm build which deletes
    dist/assets/index-OldHash.{js,css} and creates dist/assets/index-NewHash.{js,css}.
    The executor_output.json has an empty file_manifest and files_deleted (it 'just ran tests').
    Gate must PASS and emit a [GATE WARN] line, not ERR_UNACCOUNTED_DELETION.
    """
    committed = [
        "dist/assets/index-AbCdEfGh.js",
        "dist/assets/index-IjKlMnOp.css",
        "src/main.js",
    ]
    workspace, art, _ = _make_repo_with_dist_files(tmp_path, committed)

    # Simulate executor: delete old hashes, create new ones, leave src untouched
    (workspace / "dist/assets/index-AbCdEfGh.js").unlink()
    (workspace / "dist/assets/index-IjKlMnOp.css").unlink()
    (workspace / "dist/assets/index-QrStUvWx.js").write_text("new bundle\n", encoding="utf-8")
    (workspace / "dist/assets/index-YzAbCdEf.css").write_text("new styles\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "win animation: run build")

    payload = _base_executor_payload(file_manifest=["src/main.js"])
    result = _run_gate(workspace, art, payload)

    assert result == "PASS", (
        "Vite content-hash rotation should not trigger ERR_UNACCOUNTED_DELETION"
    )
    detail_path = art / executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    assert not detail_path.exists(), (
        "executor_gate_detail.json must not be written when only build artifact rotation detected"
    )


def test_vite_hash_rotation_emits_gate_warn(tmp_path, capsys):
    """Rotation auto-accounting must emit [GATE WARN] to stderr so the log is visible."""
    committed = ["dist/assets/index-Aa1Bb2Cc.js", "src/main.js"]
    workspace, art, _ = _make_repo_with_dist_files(tmp_path, committed)

    (workspace / "dist/assets/index-Aa1Bb2Cc.js").unlink()
    (workspace / "dist/assets/index-Dd3Ee4Ff.js").write_text("new\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "build")

    payload = _base_executor_payload(file_manifest=["src/main.js"])

    import io
    from contextlib import redirect_stderr
    stderr_buf = io.StringIO()
    ws_str = str(workspace) + os.sep
    art_str = str(art) + os.sep
    ps_path = str(art / "phase_state.json")
    exec_path = art / "executor_output.json"
    exec_path.write_text(json.dumps(payload), encoding="utf-8")

    stack = ExitStack()
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", ws_str))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", art_str))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps_path))
    stack.enter_context(redirect_stderr(stderr_buf))

    with stack:
        result = executor_gate_module.evaluate_executor(str(exec_path))

    assert result == "PASS"
    warn_output = stderr_buf.getvalue()
    assert "[GATE WARN]" in warn_output, "Must emit [GATE WARN] for auto-accounted build artifacts"
    assert "dist/assets/index-Aa1Bb2Cc.js" in warn_output


# ---------------------------------------------------------------------------
# Test 2: hash-named dist file deleted, directory empty → FAIL
# ---------------------------------------------------------------------------

def test_vite_hash_wipe_fails_gate(tmp_path):
    """dist/assets/ directory wiped (no replacement files) → ERR_UNACCOUNTED_DELETION.

    This is the MiniMax-wipe scenario: model deletes entire dist/assets/ directory.
    No replacement files → _is_build_artifact_rotation returns False → gate fails.
    """
    committed = [
        "dist/assets/index-AbCdEfGh.js",
        "dist/assets/index-IjKlMnOp.css",
    ]
    workspace, art, _ = _make_repo_with_dist_files(tmp_path, committed)

    # Executor deletes dist/assets entirely — no replacement files
    (workspace / "dist/assets/index-AbCdEfGh.js").unlink()
    (workspace / "dist/assets/index-IjKlMnOp.css").unlink()
    # dist/assets/ directory is now empty
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "wipe dist")

    payload = _base_executor_payload()
    result = _run_gate(workspace, art, payload)

    assert result == "FAIL"
    detail_path = art / executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    assert detail_path.exists()
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert detail.get("gate_error") == "ERR_UNACCOUNTED_DELETION"
    deletions = detail.get("unaccounted_deletions", [])
    assert "dist/assets/index-AbCdEfGh.js" in deletions
    assert "dist/assets/index-IjKlMnOp.css" in deletions


# ---------------------------------------------------------------------------
# Test 3: source file deleted alongside a safe rotation → FAIL on source only
# ---------------------------------------------------------------------------

def test_source_deletion_fails_even_with_safe_dist_rotation(tmp_path):
    """Source file deleted + dist rotation → gate fails on source, not on rotation.

    The unaccounted_deletions list must contain only the source file, not the
    rotated dist/assets hash.
    """
    committed = [
        "dist/assets/index-AbCdEfGh.js",
        "src/gamestate.js",
    ]
    workspace, art, _ = _make_repo_with_dist_files(tmp_path, committed)

    # Executor: rotate dist (OK), delete source (not OK)
    (workspace / "dist/assets/index-AbCdEfGh.js").unlink()
    (workspace / "dist/assets/index-NewHash12.js").write_text("new\n", encoding="utf-8")
    (workspace / "src/gamestate.js").unlink()
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "build + bad deletion")

    payload = _base_executor_payload()
    result = _run_gate(workspace, art, payload)

    assert result == "FAIL"
    detail_path = art / executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    assert detail_path.exists()
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    deletions = detail.get("unaccounted_deletions", [])
    assert "src/gamestate.js" in deletions, "Source deletion must be in the unaccounted list"
    assert "dist/assets/index-AbCdEfGh.js" not in deletions, (
        "Rotated dist artifact must NOT appear in unaccounted_deletions"
    )


# ---------------------------------------------------------------------------
# Test 4: non-hash-named dist file deleted → FAIL (pattern mismatch)
# ---------------------------------------------------------------------------

def test_non_hash_dist_file_deletion_fails_gate(tmp_path):
    """dist/index.html deleted → still ERR_UNACCOUNTED_DELETION.

    The exemption only applies to content-hashed bundle files (index-{hash}.{js|css}).
    dist/index.html does not match the pattern so it must still trigger a gate failure.
    """
    committed = ["dist/index.html", "src/main.js"]
    workspace, art, _ = _make_repo_with_dist_files(tmp_path, committed)

    (workspace / "dist/index.html").unlink()
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "bad: deleted dist/index.html")

    payload = _base_executor_payload(file_manifest=["src/main.js"])
    result = _run_gate(workspace, art, payload)

    assert result == "FAIL"
    detail_path = art / executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    assert detail_path.exists()
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert "dist/index.html" in detail.get("unaccounted_deletions", [])
