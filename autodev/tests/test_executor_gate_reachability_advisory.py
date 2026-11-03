"""P1 Stage F — executor gate reachability advisory.

Critical design decisions pinned by these tests:

* The check is **gated on raw_id.startswith("COMPLETE-")**. Per-phase
  reachability would cry wolf on the routine add-then-wire pattern
  (e.g. DATA-E1 adds a utility wired only in DATA-E2). The
  ``test_non_complete_phase_skips_check_entirely`` case is the
  load-bearing regression guard.
* Advisory output lives in ``executor_advisory_detail.json`` —
  a SEPARATE file from ``executor_gate_detail.json`` (the failure
  channel). The two channels never co-tenant.
* Gate exit value is ALWAYS PASS, even when warnings exist or the
  resolver crashed. No ``ERR_UNREACHABLE_MODULE`` code is ever emitted.
* Test-runner entry commands (pytest, jest, ...) emit a distinct
  ``reachability_not_applicable`` envelope, not a ``no_resolver``
  diagnostic — visibility without warning-channel noise.
"""

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from unittest.mock import patch

import pytest

import utils as utils_module
import executor_gate as executor_gate_module


def _patch_workspace(tmp_dir):
    stack = ExitStack()
    tmp_dir_with_sep = tmp_dir.rstrip(os.sep) + os.sep
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", tmp_dir_with_sep))
    ps = os.path.join(tmp_dir_with_sep.rstrip(os.sep), "phase_state.json")
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps))
    stack.enter_context(patch.object(executor_gate_module, "WORKSPACE_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(executor_gate_module, "ARTIFACTS_DIR", tmp_dir_with_sep))
    stack.enter_context(patch.object(executor_gate_module, "PHASE_STATE_FILE", ps))
    return stack


def _write_current_phase(workspace, *, raw_id, entry_point=None):
    payload = {
        "phase_number": 1,
        "detail": f"Phase {raw_id}",
        "raw_id": raw_id,
        "behavioral_verification": None,
    }
    if entry_point is not None:
        payload["entry_point"] = entry_point
    with open(os.path.join(workspace, "current_phase.json"), "w") as f:
        json.dump(payload, f)


def _git_init_with_base_commit(workspace):
    """The executor gate's deletion check needs a base commit. Initialise a
    git repo, make one commit, capture HEAD as the base."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=workspace, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "base"],
                   cwd=workspace, env=env, check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace,
                         env=env, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _write_pipeline_state(workspace, base_commit):
    parent = os.path.dirname(workspace.rstrip(os.sep))
    payload = {"phase_base_commit": base_commit}
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump(payload, f)


def _write_planner_output(workspace, tdd_paths=None):
    payload = {
        "implementation_plan": ["x"],
        "tdd_test_structure": tdd_paths or [],
        "pass_criteria": [{"condition": "x"}],
    }
    with open(os.path.join(workspace, "planner_output.json"), "w") as f:
        json.dump(payload, f)


def _stage_python_workspace(workspace, *, manifest, files):
    """files is {rel_path: body}. Writes each file under workspace and stages
    them in git so the deletion check sees a clean diff. Language-agnostic
    despite the name: the JS/TS cases use it too."""
    for rel, body in files.items():
        abs_p = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w") as f:
            f.write(body)


def _write_executor_output(workspace, file_manifest):
    payload = {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": file_manifest,
        "files_deleted": [],
    }
    with open(os.path.join(workspace, "executor_output.json"), "w") as f:
        json.dump(payload, f)


# ---------------------------------------------------------------------------
# classify_command + npx strip — registry-level smoke through the gate's import
# ---------------------------------------------------------------------------


def test_npx_strip_classifies_wrapped_tool():
    """The npx strip pre-pass is gate-critical — without it, npx vite would
    emit a false no_resolver warning."""
    from reachability import classify_command
    assert classify_command("npx vite") == "js_ts"
    assert classify_command("npx tsx app.ts") == "js_ts"
    assert classify_command("npx ts-node script.ts") == "js_ts"
    assert classify_command("npx pytest") == "test_runner"
    assert classify_command("npx") == "unsupported"


def test_pytest_entry_classified_as_test_runner():
    from reachability import classify_command
    assert classify_command("pytest tests/") == "test_runner"
    assert classify_command("vitest run") == "test_runner"
    assert classify_command("jest --watch") == "test_runner"
    assert classify_command("cargo run") == "unsupported"
    assert classify_command("python app.py") == "python"


# ---------------------------------------------------------------------------
# The load-bearing scoping test
# ---------------------------------------------------------------------------


def test_non_complete_phase_skips_check_entirely(tmp_workspace):
    """LOAD-BEARING: a non-COMPLETE phase with an entry_point and a file that
    WOULD be unreachable must produce NO advisory output. This is the entire
    mechanism Stage F uses to avoid crying wolf on the routine add-then-wire
    pattern (DATA-E1 adds; DATA-E2 wires)."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(
            tmp_workspace,
            raw_id="DATA-E1",  # NOT COMPLETE-*
            entry_point={"command": "python main.py", "ready_signal": "HTTP 200"},
        )
        _write_planner_output(tmp_workspace)
        files = {
            "main.py": "from a import x\n",
            "a.py": "",
            "dead.py": "print('would be flagged on COMPLETE')\n",
        }
        _stage_python_workspace(tmp_workspace, manifest=list(files), files=files)
        _write_executor_output(tmp_workspace, file_manifest=list(files))

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    advisory = os.path.join(tmp_workspace, "executor_advisory_detail.json")
    failure_detail = os.path.join(tmp_workspace, "executor_gate_detail.json")
    assert not os.path.exists(advisory), (
        "non-COMPLETE phase MUST short-circuit before writing advisory output"
    )
    assert not os.path.exists(failure_detail)


# ---------------------------------------------------------------------------
# COMPLETE phase — happy paths
# ---------------------------------------------------------------------------


def test_unreachable_file_does_not_fail_phase_on_complete(tmp_workspace):
    """COMPLETE-R0 with a dead file in the manifest → PASS + advisory file
    contains the unreachable summary."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(
            tmp_workspace,
            raw_id="COMPLETE-R0",
            entry_point={"command": "python main.py", "ready_signal": "HTTP 200"},
        )
        _write_planner_output(tmp_workspace)
        files = {
            "main.py": "from a import x\n",
            "a.py": "",
            "dead.py": "print('nothing imports me')\n",
        }
        _stage_python_workspace(tmp_workspace, manifest=list(files), files=files)
        _write_executor_output(tmp_workspace, file_manifest=list(files))

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    advisory_path = os.path.join(tmp_workspace, "executor_advisory_detail.json")
    assert os.path.exists(advisory_path)
    with open(advisory_path) as f:
        advisory = json.load(f)
    summary = advisory.get("reachability_summary")
    assert summary is not None
    assert summary["files"] == ["dead.py"]
    assert summary["count"] == 1
    assert summary["command"] == "python main.py"
    assert "reason_template" in summary and summary["reason_template"]
    assert advisory.get("reachability_not_applicable") is None
    assert advisory.get("reachability_diagnostics") == []
    # Failure channel MUST remain absent — channel separation.
    assert not os.path.exists(os.path.join(tmp_workspace, "executor_gate_detail.json"))


def test_fully_wired_complete_phase_emits_no_advisory(tmp_workspace):
    """Canary for WORKSPACE_DIR / project_root mismatches: a fully-wired
    workspace must produce ZERO advisory output. If the resolver's base
    path is out of sync with the manifest's base, every file would flag
    unreachable and we'd see all three in a summary."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(
            tmp_workspace,
            raw_id="COMPLETE-R0",
            entry_point={"command": "python main.py", "ready_signal": "HTTP 200"},
        )
        _write_planner_output(tmp_workspace)
        files = {
            "main.py": "from a import x\n",
            "a.py": "from b import y\n",
            "b.py": "",
        }
        _stage_python_workspace(tmp_workspace, manifest=list(files), files=files)
        _write_executor_output(tmp_workspace, file_manifest=list(files))

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    assert not os.path.exists(
        os.path.join(tmp_workspace, "executor_advisory_detail.json")
    )


def test_resolver_crash_downgrades_to_resolver_error_diagnostic(tmp_workspace):
    """The entry script doesn't exist on disk → resolver returns
    entry_resolved=None → gate-helper emits a resolver_error diagnostic.
    Gate still PASSes."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(
            tmp_workspace,
            raw_id="COMPLETE-R0",
            entry_point={"command": "python nonexistent_entry.py",
                         "ready_signal": "HTTP 200"},
        )
        _write_planner_output(tmp_workspace)
        # Manifest contains a file that exists; no main.py.
        files = {"a.py": ""}
        _stage_python_workspace(tmp_workspace, manifest=list(files), files=files)
        _write_executor_output(tmp_workspace, file_manifest=list(files))

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    advisory_path = os.path.join(tmp_workspace, "executor_advisory_detail.json")
    assert os.path.exists(advisory_path)
    with open(advisory_path) as f:
        advisory = json.load(f)
    assert advisory.get("reachability_summary") is None
    diagnostics = advisory.get("reachability_diagnostics") or []
    assert any(d.get("kind") == "resolver_error" for d in diagnostics)


def test_pytest_entry_emits_not_applicable_signal(tmp_workspace):
    """Test-runner entries take a distinct path — reachability_not_applicable,
    NOT a no_resolver warning."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(
            tmp_workspace,
            raw_id="COMPLETE-R0",
            entry_point={"command": "pytest tests/", "ready_signal": "exit 0"},
        )
        _write_planner_output(tmp_workspace)
        _stage_python_workspace(tmp_workspace, manifest=[], files={})
        _write_executor_output(tmp_workspace, file_manifest=[])

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    advisory_path = os.path.join(tmp_workspace, "executor_advisory_detail.json")
    assert os.path.exists(advisory_path)
    with open(advisory_path) as f:
        advisory = json.load(f)
    not_applicable = advisory.get("reachability_not_applicable")
    assert not_applicable is not None
    assert "test runner" in not_applicable["reason"].lower()
    assert "pytest" in not_applicable["reason"]
    # No diagnostic and no summary — the entire signal is "consciously skipped."
    assert advisory.get("reachability_summary") is None
    assert advisory.get("reachability_diagnostics") == []


def test_existing_executor_gate_tests_unchanged_when_no_entry_point(tmp_workspace):
    """A COMPLETE phase WITHOUT an entry_point (e.g. legacy verification.md
    that doesn't define one) must produce no advisory output — the helper
    short-circuits on missing command."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(tmp_workspace, raw_id="COMPLETE-R0", entry_point=None)
        _write_planner_output(tmp_workspace)
        _stage_python_workspace(tmp_workspace, manifest=[], files={})
        _write_executor_output(tmp_workspace, file_manifest=[])

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    assert not os.path.exists(
        os.path.join(tmp_workspace, "executor_advisory_detail.json")
    )


def test_unsupported_language_emits_no_resolver_diagnostic(tmp_workspace):
    """COMPLETE phase with a cargo entry → no_resolver diagnostic, never an
    ERR_* code. Phase still passes."""
    with _patch_workspace(tmp_workspace):
        base = _git_init_with_base_commit(tmp_workspace)
        _write_pipeline_state(tmp_workspace, base)
        _write_current_phase(
            tmp_workspace,
            raw_id="COMPLETE-R0",
            entry_point={"command": "cargo run", "ready_signal": "listening"},
        )
        _write_planner_output(tmp_workspace)
        _stage_python_workspace(tmp_workspace, manifest=[], files={})
        _write_executor_output(tmp_workspace, file_manifest=[])

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    advisory_path = os.path.join(tmp_workspace, "executor_advisory_detail.json")
    assert os.path.exists(advisory_path)
    with open(advisory_path) as f:
        advisory = json.load(f)
    diagnostics = advisory.get("reachability_diagnostics") or []
    assert any(d.get("kind") == "no_resolver" for d in diagnostics)


# ---------------------------------------------------------------------------
# Manifest filter — only files the active resolver could ever reach
# ---------------------------------------------------------------------------


def _run_complete_phase(workspace, command, files):
    """Stage a COMPLETE-R0 phase whose manifest is every key of ``files`` and
    return the advisory summary (``None`` when no advisory file was written)."""
    with _patch_workspace(workspace):
        base = _git_init_with_base_commit(workspace)
        _write_pipeline_state(workspace, base)
        _write_current_phase(
            workspace,
            raw_id="COMPLETE-R0",
            entry_point={"command": command, "ready_signal": "HTTP 200"},
        )
        _write_planner_output(workspace)
        _stage_python_workspace(workspace, manifest=list(files), files=files)
        _write_executor_output(workspace, file_manifest=list(files))

        result = executor_gate_module.evaluate_executor()

    assert result == "PASS"
    advisory_path = os.path.join(workspace, "executor_advisory_detail.json")
    if not os.path.exists(advisory_path):
        return None
    with open(advisory_path) as f:
        return json.load(f).get("reachability_summary")


def test_non_source_and_test_files_not_flagged(tmp_workspace):
    """A Python resolver can never reach a README, a stylesheet or a test
    file, so none of them may be reported as unreached."""
    files = {
        "main.py": "from a import x\n",
        "a.py": "",
        "README.md": "# App\n",
        "static/style.css": "body {}\n",
        "tests/test_main.py": "def test_x(): pass\n",
        "test_a.py": "def test_a(): pass\n",
        "a_test.py": "def test_b(): pass\n",
        "conftest.py": "",
    }
    assert _run_complete_phase(tmp_workspace, "python main.py", files) is None


def test_ts_file_under_python_entry_not_flagged(tmp_workspace):
    """A .ts file is outside the Python resolver's extensions, so it is never
    compared against a Python walk."""
    files = {
        "main.py": "",
        "web/app.ts": "export const x = 1;\n",
    }
    assert _run_complete_phase(tmp_workspace, "python main.py", files) is None


def test_real_orphan_still_flagged_alongside_filtered_files(tmp_workspace):
    """The filter drops only what the resolver can't reach: a genuine Python
    orphan is still the one file in the summary."""
    files = {
        "main.py": "from a import x\n",
        "a.py": "",
        "dead.py": "print('nothing imports me')\n",
        "README.md": "# App\n",
        "tests/test_main.py": "def test_x(): pass\n",
    }
    summary = _run_complete_phase(tmp_workspace, "python main.py", files)
    assert summary is not None
    assert summary["files"] == ["dead.py"]
    assert summary["count"] == 1


def test_package_init_and_submodule_not_flagged(tmp_workspace):
    """Importing pkg.models runs pkg/__init__.py, and `from pkg import views`
    imports pkg/views.py: neither is an orphan."""
    files = {
        "main.py": "from pkg.models import M\nfrom pkg import views\n",
        "pkg/__init__.py": "",
        "pkg/models.py": "M = 1\n",
        "pkg/views.py": "",
    }
    assert _run_complete_phase(tmp_workspace, "python main.py", files) is None


def test_js_test_and_spec_files_not_flagged(tmp_workspace):
    """JS/TS test conventions (*.test.*, *.spec.*, __tests__/) are dropped; a
    real JS orphan is still flagged."""
    files = {
        "index.js": "import { x } from './src/a.js';\n",
        "src/a.js": "export const x = 1;\n",
        "src/a.test.js": "test('x', () => {});\n",
        "src/a.spec.ts": "test('x', () => {});\n",
        "__tests__/b.js": "test('b', () => {});\n",
        "src/styles.css": "body {}\n",
        "src/types.d.ts": "export type T = number;\n",
        "src/orphan.ts": "export const y = 2;\n",
    }
    summary = _run_complete_phase(tmp_workspace, "node index.js", files)
    assert summary is not None
    assert summary["files"] == ["src/orphan.ts"]


@pytest.mark.parametrize("path, exts, expected", [
    ("app/models.py", (".py",), True),
    ("README.md", (".py",), False),
    ("web/app.ts", (".py",), False),
    ("tests/test_app.py", (".py",), False),
    ("pkg/test/helpers.py", (".py",), False),
    ("test_app.py", (".py",), False),
    ("app_test.py", (".py",), False),
    ("conftest.py", (".py",), False),
    ("src/latest.py", (".py",), True),
    ("src/contest.py", (".py",), True),
    ("src/app.test.tsx", (".ts", ".tsx"), False),
    ("src/app.spec.js", (".js",), False),
    ("src/__tests__/app.js", (".js",), False),
    ("src\\app.ts", (".ts",), True),
    ("notes.txt", (), True),
    ("src/types.d.ts", (".ts",), False),
    ("Tests/x.py", (".py",), False),
    ("Test_foo.py", (".py",), False),
    ("MAIN.PY", (".py",), True),
    ("pkg/__init__.py", (".py",), True),
])
def test_is_reachability_candidate(path, exts, expected):
    assert executor_gate_module._is_reachability_candidate(path, exts) is expected


# ---------------------------------------------------------------------------
# Anti-regression: no ERR_UNREACHABLE_MODULE anywhere
# ---------------------------------------------------------------------------


def test_no_err_unreachable_module_in_gate_source():
    """P3 Stage A is where ERR_UNREACHABLE_MODULE will live. In P1 Stage F,
    the executor gate must NEVER emit this code — the static-lint guards
    against an accidental promotion."""
    gate_src_path = os.path.abspath(executor_gate_module.__file__)
    with open(gate_src_path) as f:
        src = f.read()
    assert "ERR_UNREACHABLE_MODULE" not in src, (
        "ERR_UNREACHABLE_MODULE is reserved for P3 Stage A's blocking promotion. "
        "Stage F is advisory-only."
    )
