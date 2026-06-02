"""Regression guard: the executor gate no longer enforces PRD-verbatim anchors.

The ``prd_verbatim`` concept was scrapped (it verified vocabulary, not
function — it grepped ``git ls-files`` for literal anchor strings and, because
``prd.md`` is itself tracked, "passed" anchors by matching the PRD rather than
the code, while hard-failing correct work over formatting). This test pins the
post-scrub behaviour: a planner ``pass_criteria`` entry with
``traces_to: "prd_verbatim:<literal>"`` whose literal is ABSENT from every
tracked file must NOT fail the executor gate — the gate ignores the anchor form
entirely and advances. If anyone reintroduces verbatim enforcement, the gate
returns FAIL with ``ERR_PRD_VERBATIM_MISSING`` and this test goes red.

Fixture layout mirrors the (removed) ``test_executor_gate_prd_verbatim.py``:
the temp workspace doubles as workspace + artifacts dir; the repo is
git-init'd and committed so the deletion guard's ``git diff <base> HEAD`` is
empty (HEAD == phase_base_commit), exercising the full PASS path.
"""

import json
import os
import subprocess
from contextlib import ExitStack
from unittest.mock import patch

import utils as utils_module
import executor_gate as executor_gate_module


def _patch_workspace(tmp_dir):
    """Point both modules' WORKSPACE_DIR / ARTIFACTS_DIR / PHASE_STATE_FILE at
    the flat temp workspace (mirrors the sibling gate tests)."""
    stack = ExitStack()
    tmp_dir_with_sep = tmp_dir.rstrip(os.sep) + os.sep
    ps = os.path.join(tmp_dir_with_sep.rstrip(os.sep), "phase_state.json")
    for mod in (utils_module, executor_gate_module):
        stack.enter_context(patch.object(mod, "WORKSPACE_DIR", tmp_dir_with_sep))
        stack.enter_context(patch.object(mod, "ARTIFACTS_DIR", tmp_dir_with_sep))
        stack.enter_context(patch.object(mod, "PHASE_STATE_FILE", ps))
    return stack


def _git_init_and_commit(workspace):
    """Init a deterministic git repo and commit everything currently present.
    Returns the HEAD SHA so the caller can set phase_base_commit == HEAD, which
    makes the deletion guard's diff empty."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=workspace, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace, capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_gate_does_not_emit_err_prd_verbatim_missing(tmp_workspace):
    """A prd_verbatim anchor whose literal is absent from all tracked source
    must NOT fail the gate post-scrub. Otherwise-valid executor output, so the
    only thing the (removed) check would have tripped on is the missing anchor.
    Pre-scrub this returned FAIL/ERR_PRD_VERBATIM_MISSING; post-scrub it PASSes."""
    # Tracked source that does NOT contain the anchor literal.
    src = os.path.join(tmp_workspace, "src", "app.py")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w") as f:
        f.write('label = "Hello world"\n')
    head = _git_init_and_commit(tmp_workspace)

    # Deletion guard reads phase_base_commit one level up; HEAD == base → empty diff.
    parent = os.path.dirname(tmp_workspace.rstrip(os.sep))
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump({"phase_base_commit": head}, f)

    # Planner declares a prd_verbatim anchor whose literal appears nowhere in source.
    with open(os.path.join(tmp_workspace, "planner_output.json"), "w") as f:
        json.dump(
            {
                "implementation_plan": ["x"],
                "tdd_test_structure": [],
                "pass_criteria": [
                    {"condition": "surface the tagline",
                     "traces_to": "prd_verbatim:Start the game"}
                ],
            },
            f,
        )

    # Otherwise gate-valid executor output (manifest file exists; no behavioral
    # block on disk so that check is skipped; tests claim passing).
    out_path = os.path.join(tmp_workspace, "executor_output.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "status": "complete",
                "tests_written": [],
                "test_results": {"all_passing": True},
                "file_manifest": ["src/app.py"],
                "files_deleted": [],
            },
            f,
        )

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(out_path)

    assert result == "PASS", (
        f"prd_verbatim enforcement was scrapped — a missing anchor must not "
        f"fail the gate; got {result!r}"
    )
    state_path = os.path.join(tmp_workspace, "phase_state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
        assert state.get("last_error_code") != "ERR_PRD_VERBATIM_MISSING", (
            "the scrapped check must not stamp ERR_PRD_VERBATIM_MISSING into phase_state"
        )
