"""P1 Stage C — executor-gate PRD-verbatim anchor enforcement.

The planner emits ``pass_criteria[].traces_to: "prd_verbatim:<literal>"`` for
criteria that must appear character-for-character in the build. The executor
gate enforces this contract: for every such anchor, the literal string must
be present in at least one git-tracked file in the workspace. If any anchor
is missing, the gate fails with ``ERR_PRD_VERBATIM_MISSING`` and writes the
missing anchors to ``executor_gate_detail.json``.

Test fixtures mirror ``test_executor_gate_behavioral_artifacts.py:24-115`` —
flat workspace layout (``tmp_workspace`` doubles as workspace + artifacts
dir), state file written one level up via ``_write_pipeline_state``. Each
test ``git init``'s the workspace and commits the controlled fixture files
so ``git ls-files`` and ``grep -F`` operate on real on-disk state (no
subprocess stub — the helpers under test ARE subprocess calls).
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


# ---------------------------------------------------------------------------
# Shared helpers — mirrors test_executor_gate_behavioral_artifacts.py
# ---------------------------------------------------------------------------


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


def _write_pipeline_state(workspace, base_commit):
    """The executor gate's deletion check reads ``phase_base_commit`` from
    ``pipeline_state.json`` at the parent of WORKSPACE_DIR. Writing the
    actual HEAD SHA here makes ``git diff <head> HEAD`` empty, so the
    deletion check passes and our new check is exercised."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    payload = {"phase_base_commit": base_commit}
    with open(os.path.join(parent, "pipeline_state.json"), "w") as f:
        json.dump(payload, f)


def _git_init_workspace(workspace):
    """Initialise an empty git repo at ``workspace`` with a deterministic
    identity so commit hashes are reproducible across test runs on different
    machines. Returns nothing — the workspace is left in a usable state for
    add/commit calls."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"], cwd=workspace, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)


def _git_commit_all(workspace, message="seed"):
    """Stage every file currently in the workspace and commit. Returns the
    resulting HEAD SHA so the caller can write it into pipeline_state."""
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=workspace, check=True
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return head


def _make_tracked_file(workspace, rel_path, content):
    """Write the file at rel_path with content, but do NOT commit. Caller
    is expected to commit at the end of fixture setup."""
    abs_path = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(abs_path) or workspace, exist_ok=True)
    with open(abs_path, "w") as f:
        f.write(content)
    return rel_path


def _write_planner_output_with_anchors(workspace, anchors, *, extra_criteria=None):
    """Emit ``planner_output.json`` with one prd_verbatim entry per anchor.
    ``extra_criteria`` may carry additional pass_criteria dicts (e.g. tdd:
    or behavior: anchors) to test mixed anchor types."""
    pass_criteria = [
        {"condition": f"surface anchor {a!r}", "traces_to": f"prd_verbatim:{a}"}
        for a in anchors
    ]
    if extra_criteria:
        pass_criteria = list(extra_criteria) + pass_criteria
    payload = {
        "implementation_plan": ["x"],
        "tdd_test_structure": [],
        "pass_criteria": pass_criteria or [{"condition": "x"}],
    }
    with open(os.path.join(workspace, "planner_output.json"), "w") as f:
        json.dump(payload, f)


def _executor_output(workspace, *, file_manifest=None):
    """Baseline executor output. file_manifest entries are created on disk
    so the existing manifest-existence check passes; tests that need to
    add their own tracked files do so separately before commit."""
    file_manifest = file_manifest if file_manifest is not None else []
    for rel in file_manifest:
        abs_p = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(abs_p) or workspace, exist_ok=True)
        if not os.path.exists(abs_p):
            open(abs_p, "w").close()
    return {
        "status": "complete",
        "tests_written": [],
        "test_results": {"all_passing": True},
        "file_manifest": file_manifest,
        "files_deleted": [],
    }


def _write_executor_output(workspace, payload):
    """Persist the executor output JSON at the canonical path and return the path."""
    out_path = os.path.join(workspace, "executor_output.json")
    with open(out_path, "w") as f:
        json.dump(payload, f)
    return out_path


# ---------------------------------------------------------------------------
# §3 row 1 — happy path: anchor present in tracked source → PASS
# ---------------------------------------------------------------------------


def test_anchor_present_in_tracked_source_passes(tmp_workspace):
    """§3 row 1 — Tracked file ``src/app.py`` contains literal
    ``"Start the game"``; planner anchors that literal via
    ``prd_verbatim:Start the game``. The gate must return PASS and
    must not set ``last_error_code`` to ``ERR_PRD_VERBATIM_MISSING``.
    Pins the happy path so the new check does not regress into a
    no-op that always passes."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(
        tmp_workspace, "src/app.py", 'label = "Start the game"\n'
    )
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(tmp_workspace, ["Start the game"])

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "PASS", (
        f"Anchor present in tracked source must pass the gate; got {result!r}"
    )
    state_path = os.path.join(tmp_workspace, "phase_state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
        assert state.get("last_error_code") != "ERR_PRD_VERBATIM_MISSING", (
            "PASS path must not stamp ERR_PRD_VERBATIM_MISSING into phase_state"
        )


# ---------------------------------------------------------------------------
# §3 row 2 — anchor missing → FAIL with ERR_PRD_VERBATIM_MISSING + detail
# ---------------------------------------------------------------------------


def test_anchor_missing_returns_err_prd_verbatim_missing(tmp_workspace):
    """§3 row 2 — Tracked file does NOT contain the literal. Gate must
    return FAIL, set ``last_error_code = ERR_PRD_VERBATIM_MISSING``, and
    write ``executor_gate_detail.json`` with ``{gate_error,
    missing_anchors}``. Core enforcement contract."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(
        tmp_workspace, "src/app.py", 'label = "Hello world"\n'
    )
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(tmp_workspace, ["Start the game"])

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "FAIL"
    with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
        state = json.load(f)
    assert state.get("last_error_code") == "ERR_PRD_VERBATIM_MISSING"

    detail_path = os.path.join(
        tmp_workspace, executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    )
    assert os.path.exists(detail_path), (
        "executor_gate_detail.json must be written so the orchestrator can "
        "merge missing_anchors into failure_context.json"
    )
    with open(detail_path) as f:
        detail = json.load(f)
    assert detail.get("gate_error") == "ERR_PRD_VERBATIM_MISSING"
    assert detail.get("missing_anchors") == ["Start the game"]


# ---------------------------------------------------------------------------
# §3 row 3 — untracked file containing the anchor is NOT enough
# ---------------------------------------------------------------------------


def test_anchor_found_only_in_untracked_file_fails(tmp_workspace):
    """§3 row 3 — Anchor literal is present only in
    ``node_modules/foo.js`` (untracked), and no tracked file contains
    it. Gate must FAIL with ``ERR_PRD_VERBATIM_MISSING``. Pins the
    ``git ls-files`` (tracked-only) contract — catches a future
    refactor that swaps the file-source helper for an undiscriminating
    recursive walk."""
    _git_init_workspace(tmp_workspace)
    # tracked source file — does NOT contain anchor
    _make_tracked_file(
        tmp_workspace, "src/app.py", 'label = "different copy"\n'
    )
    head = _git_commit_all(tmp_workspace, "initial")

    # AFTER the commit, drop an untracked file that contains the anchor.
    # `git ls-files` will not include it.
    _make_tracked_file(
        tmp_workspace, "node_modules/foo.js", 'const x = "Start the game";\n'
    )

    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(tmp_workspace, ["Start the game"])

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "FAIL"
    with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
        state = json.load(f)
    assert state.get("last_error_code") == "ERR_PRD_VERBATIM_MISSING"


# ---------------------------------------------------------------------------
# §3 row 4 — no prd_verbatim: anchors → check is a no-op
# ---------------------------------------------------------------------------


def test_no_prd_verbatim_anchors_skips_check(tmp_workspace):
    """§3 row 4 — ``pass_criteria`` contains only ``tdd:`` and
    ``behavior:`` anchors; no ``prd_verbatim:``. Gate must PASS and must
    not stamp ``ERR_PRD_VERBATIM_MISSING``. Pins the no-op contract —
    catches a future refactor that always-runs the check even with zero
    anchors."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(tmp_workspace, "src/app.py", "placeholder\n")
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)

    # pass_criteria with only non-prd_verbatim anchors
    _write_planner_output_with_anchors(
        tmp_workspace,
        anchors=[],
        extra_criteria=[
            {"condition": "All tests pass", "traces_to": "tdd:tests/test_x.py"},
            {"condition": "User sees x", "traces_to": "behavior:user_observable"},
        ],
    )

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "PASS", (
        f"Phase with only tdd:/behavior: anchors must skip the prd_verbatim "
        f"check; got {result!r}"
    )
    state_path = os.path.join(tmp_workspace, "phase_state.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
        assert state.get("last_error_code") != "ERR_PRD_VERBATIM_MISSING"


# ---------------------------------------------------------------------------
# §3 row 5 — regex metachars treated literally → present anchor PASSES
# ---------------------------------------------------------------------------


def test_anchor_with_regex_metachars_treated_literally(tmp_workspace):
    """§3 row 5 — Anchor ``foo.*bar(baz)+``; tracked file contains
    exactly that literal string. Gate must PASS. Pins ``grep -F``
    fixed-string semantics — verifies regex metacharacters in anchors
    are not interpreted as regex."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(
        tmp_workspace, "src/app.py", 'label = "foo.*bar(baz)+"\n'
    )
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(tmp_workspace, ["foo.*bar(baz)+"])

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "PASS", (
        f"Literal anchor containing regex metachars must match itself "
        f"verbatim under grep -F; got {result!r}"
    )


# ---------------------------------------------------------------------------
# §3 row 6 — regex metachars: regex-only match should NOT count
# ---------------------------------------------------------------------------


def test_anchor_with_regex_metachars_no_literal_match_fails(tmp_workspace):
    """§3 row 6 — Same anchor ``foo.*bar(baz)+``; tracked file contains
    only ``fooXbar`` (which would match the anchor as a regex but is NOT
    a literal substring match). Gate must FAIL with
    ``ERR_PRD_VERBATIM_MISSING``. Inverse-direction half of the ``-F``
    check; together with row 5 pins both directions."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(tmp_workspace, "src/app.py", 'label = "fooXbar"\n')
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(tmp_workspace, ["foo.*bar(baz)+"])

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "FAIL"
    with open(os.path.join(tmp_workspace, "phase_state.json")) as f:
        state = json.load(f)
    assert state.get("last_error_code") == "ERR_PRD_VERBATIM_MISSING"


# ---------------------------------------------------------------------------
# §3 row 7 — multi-anchor happy path
# ---------------------------------------------------------------------------


def test_multiple_anchors_all_present_passes(tmp_workspace):
    """§3 row 7 — Two anchors; both literals present in tracked files
    (one per file). Gate must PASS. Pins the multi-anchor loop."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(
        tmp_workspace, "src/app.py", 'title = "Start the game"\n'
    )
    _make_tracked_file(
        tmp_workspace, "src/ui.py", 'btn_label = "Quit to lobby"\n'
    )
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(
        tmp_workspace, ["Start the game", "Quit to lobby"]
    )

    out = _executor_output(
        tmp_workspace, file_manifest=["src/app.py", "src/ui.py"]
    )
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "PASS"


# ---------------------------------------------------------------------------
# §3 row 8 — only the missing anchor is reported
# ---------------------------------------------------------------------------


def test_multiple_anchors_one_missing_fails_with_only_missing_listed(tmp_workspace):
    """§3 row 8 — Two anchors; one present, one missing. Gate must FAIL
    and ``executor_gate_detail.json`` ``missing_anchors`` must contain
    ONLY the missing anchor, not both. Reporting hygiene — operators
    need to know which specific anchor failed."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(
        tmp_workspace, "src/app.py", 'title = "Start the game"\n'
    )
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)
    _write_planner_output_with_anchors(
        tmp_workspace, ["Start the game", "Quit to lobby"]
    )

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        result = executor_gate_module.evaluate_executor(output_path)

    assert result == "FAIL"
    detail_path = os.path.join(
        tmp_workspace, executor_gate_module.EXECUTOR_GATE_DETAIL_JSON
    )
    with open(detail_path) as f:
        detail = json.load(f)
    assert detail.get("gate_error") == "ERR_PRD_VERBATIM_MISSING"
    assert detail.get("missing_anchors") == ["Quit to lobby"], (
        f"Only the missing anchor must appear in missing_anchors; "
        f"got {detail.get('missing_anchors')!r}"
    )


# ---------------------------------------------------------------------------
# §3 row 9 — defensive guard when planner_output.json is absent
# ---------------------------------------------------------------------------


def test_no_planner_output_file_skips_check_gracefully(tmp_workspace):
    """§3 row 9 — Delete ``planner_output.json`` before the gate runs.
    The new check rides inside the existing ``if planner_data is not
    None:`` guard, so absent planner output must NOT crash on
    ``pass_criteria`` access and must NOT stamp
    ``ERR_PRD_VERBATIM_MISSING``."""
    _git_init_workspace(tmp_workspace)
    _make_tracked_file(tmp_workspace, "src/app.py", "placeholder\n")
    head = _git_commit_all(tmp_workspace, "initial")
    _write_pipeline_state(tmp_workspace, base_commit=head)

    # Intentionally do NOT write planner_output.json.
    planner_path = os.path.join(tmp_workspace, "planner_output.json")
    if os.path.exists(planner_path):
        os.remove(planner_path)

    out = _executor_output(tmp_workspace, file_manifest=["src/app.py"])
    output_path = _write_executor_output(tmp_workspace, out)

    with _patch_workspace(tmp_workspace):
        # No crash: load_json_safe will return None; gate stamps
        # ERR_FILE_MISSING for planner_output but does not return FAIL.
        result = executor_gate_module.evaluate_executor(output_path)

    state_path = os.path.join(tmp_workspace, "phase_state.json")
    last_error = None
    if os.path.exists(state_path):
        with open(state_path) as f:
            last_error = json.load(f).get("last_error_code")
    assert last_error != "ERR_PRD_VERBATIM_MISSING", (
        f"Absent planner_output must not stamp ERR_PRD_VERBATIM_MISSING; "
        f"got {last_error!r}"
    )
    # The verdict may be PASS (deletion check empty) or it may fail on an
    # unrelated check — what matters is no crash and no false positive.
    assert result in ("PASS", "FAIL")
