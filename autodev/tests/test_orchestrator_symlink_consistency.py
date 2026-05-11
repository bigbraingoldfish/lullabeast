"""ISSUE A: Pre-invocation symlink consistency guard.

update_symlink() correctly updates both pipeline-project symlinks
(AUTODEV_PIPELINE_ROOT/pipeline-project and OPENCLAW_ROOT/pipeline-project),
but there is no assertion before agent invocation that the two symlinks
actually resolve to the same directory as pipeline_state.project_path.

Operator-applied manual `ln -sfn` (common during diagnostic recovery) can
silently diverge them, causing the executor to write sentinels to a different
directory than the orchestrator polls.

Fix: add _verify_symlinks_consistent(project_path) and call it immediately
before the executor and reviewer webhook invocations. When a symlink_fixer is
passed (self.update_symlink), divergence triggers auto-reconcile to project_path.

Fix-ID: symlink-consistency-guard (A)
"""

import ast
import os
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_orchestrator():
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    return ast.parse(source), source


def _branch_calls_before_webhook(branch_label: str):
    """Return Call func names that appear in `branch_label` branch, in line order.

    Only collects from the topmost If node matching `current_agent == branch_label`
    so we don't accidentally pick up calls from nested blocks.
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    result = []  # (name, lineno)

    class V(ast.NodeVisitor):
        def __init__(self):
            self._in_branch = False

        def visit_If(self, node):
            test = node.test
            is_branch = (
                isinstance(test, ast.Compare)
                and isinstance(test.ops[0], ast.Eq)
                and (
                    (isinstance(test.left, ast.Constant) and test.left.value == branch_label)
                    or (
                        test.comparators
                        and isinstance(test.comparators[0], ast.Constant)
                        and test.comparators[0].value == branch_label
                    )
                )
            )
            if is_branch:
                prev = self._in_branch
                self._in_branch = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        f = child.func
                        name = (
                            f.id if isinstance(f, ast.Name)
                            else (f.attr if isinstance(f, ast.Attribute) else "")
                        )
                        if name:
                            result.append((name, getattr(child, "lineno", -1)))
                self._in_branch = prev
            else:
                self.generic_visit(node)

    V().visit(tree)
    return result


# ---------------------------------------------------------------------------
# Static-AST tests
# ---------------------------------------------------------------------------


def test_verify_symlinks_consistent_function_defined():
    """orchestrator.py must define a _verify_symlinks_consistent function."""
    _, source = _parse_orchestrator()
    assert "_verify_symlinks_consistent" in source, (
        "orchestrator.py must define _verify_symlinks_consistent(project_path) "
        "to detect symlink divergence before agent invocation. "
        "ISSUE-A not yet implemented."
    )


def test_verify_symlinks_consistent_called_before_executor_webhook():
    """_verify_symlinks_consistent must be called in the executor branch."""
    calls = _branch_calls_before_webhook("executor")
    names = [n for n, _ in calls]
    assert "_verify_symlinks_consistent" in names, (
        "_verify_symlinks_consistent must be called in the executor branch before "
        "the agent webhook fires, so symlink divergence is logged before it causes "
        f"a sentinel miss. Calls found in executor branch: {names}. "
        "ISSUE-A not yet implemented."
    )


def test_verify_symlinks_consistent_called_before_reviewer_webhook():
    """_verify_symlinks_consistent must be called in the reviewer branch."""
    calls = _branch_calls_before_webhook("reviewer")
    names = [n for n, _ in calls]
    assert "_verify_symlinks_consistent" in names, (
        "_verify_symlinks_consistent must be called in the reviewer branch before "
        "the agent webhook fires. "
        f"Calls found in reviewer branch: {names}. "
        "ISSUE-A not yet implemented."
    )


# ---------------------------------------------------------------------------
# Unit tests for the helper function itself
# ---------------------------------------------------------------------------


def _import_verify_fn():
    import orchestrator  # noqa: F401
    return orchestrator._verify_symlinks_consistent


def test_verify_returns_true_when_both_symlinks_match(tmp_path):
    """Returns True and logs nothing when both symlinks resolve to project_path."""
    import orchestrator

    project = tmp_path / "myproject"
    project.mkdir()
    sl_autodev = tmp_path / "pipeline-project-autodev"
    sl_openclaw = tmp_path / "pipeline-project-openclaw"
    sl_autodev.symlink_to(project)
    sl_openclaw.symlink_to(project)

    original_st = orchestrator.SYMLINK_TARGET
    original_oc = orchestrator.OPENCLAW_ROOT
    try:
        orchestrator.SYMLINK_TARGET = str(sl_autodev)
        orchestrator.OPENCLAW_ROOT = str(tmp_path)
        # Create openclaw symlink at the expected sub-path
        (tmp_path / "pipeline-project").symlink_to(project)
        result = orchestrator._verify_symlinks_consistent(str(project))
    finally:
        orchestrator.SYMLINK_TARGET = original_st
        orchestrator.OPENCLAW_ROOT = original_oc

    assert result is True


def test_verify_returns_false_when_autodev_symlink_diverges(tmp_path, capsys):
    """Returns False and prints [WARN] when AUTODEV symlink resolves to wrong dir."""
    import orchestrator

    project = tmp_path / "myproject"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    sl_autodev = tmp_path / "pipeline-project-autodev"
    sl_autodev.symlink_to(other)        # diverged
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    (oc_dir / "pipeline-project").symlink_to(project)

    original_st = orchestrator.SYMLINK_TARGET
    original_oc = orchestrator.OPENCLAW_ROOT
    try:
        orchestrator.SYMLINK_TARGET = str(sl_autodev)
        orchestrator.OPENCLAW_ROOT = str(oc_dir)
        result = orchestrator._verify_symlinks_consistent(str(project))
    finally:
        orchestrator.SYMLINK_TARGET = original_st
        orchestrator.OPENCLAW_ROOT = original_oc

    assert result is False
    captured = capsys.readouterr()
    assert "[WARN]" in captured.out or "[WARN]" in captured.err, (
        "_verify_symlinks_consistent must print a [WARN] when symlinks diverge. "
        f"stdout={captured.out!r} stderr={captured.err!r}"
    )


def test_verify_does_not_call_fixer_when_already_consistent(tmp_path):
    """When symlinks already match project_path, fixer must not run."""
    import orchestrator

    project = tmp_path / "myproject"
    project.mkdir()
    sl_autodev = tmp_path / "pipeline-project-autodev"
    sl_autodev.symlink_to(project)
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    (oc_dir / "pipeline-project").symlink_to(project)

    fixer = MagicMock(return_value=True)
    original_st = orchestrator.SYMLINK_TARGET
    original_oc = orchestrator.OPENCLAW_ROOT
    try:
        orchestrator.SYMLINK_TARGET = str(sl_autodev)
        orchestrator.OPENCLAW_ROOT = str(oc_dir)
        result = orchestrator._verify_symlinks_consistent(str(project), fixer)
    finally:
        orchestrator.SYMLINK_TARGET = original_st
        orchestrator.OPENCLAW_ROOT = original_oc

    assert result is True
    fixer.assert_not_called()


def test_verify_reconciles_and_returns_true_when_fixer_succeeds(tmp_path, capsys):
    """Spy fixer repairs symlinks; verify passes normalized path and returns True."""
    import orchestrator

    project = tmp_path / "myproject"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    sl_autodev = tmp_path / "pipeline-project-autodev"
    sl_autodev.symlink_to(other)
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    (oc_dir / "pipeline-project").symlink_to(project)

    expected_target = os.path.abspath(str(project))
    called_with: list[str] = []

    def spy_fixer(p: str) -> bool:
        called_with.append(p)
        subprocess.run(["ln", "-sfn", str(project), str(sl_autodev)], check=True)
        subprocess.run(["ln", "-sfn", str(project), str(oc_dir / "pipeline-project")], check=True)
        return True

    original_st = orchestrator.SYMLINK_TARGET
    original_oc = orchestrator.OPENCLAW_ROOT
    try:
        orchestrator.SYMLINK_TARGET = str(sl_autodev)
        orchestrator.OPENCLAW_ROOT = str(oc_dir)
        result = orchestrator._verify_symlinks_consistent(str(project), spy_fixer)
    finally:
        orchestrator.SYMLINK_TARGET = original_st
        orchestrator.OPENCLAW_ROOT = original_oc

    assert result is True
    assert called_with == [expected_target]
    assert os.path.realpath(sl_autodev) == expected_target
    assert os.path.realpath(oc_dir / "pipeline-project") == expected_target
    out = capsys.readouterr().out
    assert "[RECONCILE]" in out


def test_verify_returns_false_when_fixer_returns_false(tmp_path, capsys):
    """When fixer returns False, verify returns False and logs [ERROR]."""
    import orchestrator

    project = tmp_path / "myproject"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    sl_autodev = tmp_path / "pipeline-project-autodev"
    sl_autodev.symlink_to(other)
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    (oc_dir / "pipeline-project").symlink_to(project)

    original_st = orchestrator.SYMLINK_TARGET
    original_oc = orchestrator.OPENCLAW_ROOT
    try:
        orchestrator.SYMLINK_TARGET = str(sl_autodev)
        orchestrator.OPENCLAW_ROOT = str(oc_dir)
        result = orchestrator._verify_symlinks_consistent(
            str(project), lambda _p: False
        )
    finally:
        orchestrator.SYMLINK_TARGET = original_st
        orchestrator.OPENCLAW_ROOT = original_oc

    assert result is False
    captured = capsys.readouterr()
    assert "[ERROR]" in captured.out or "[ERROR]" in captured.err


def test_verify_returns_false_when_project_path_empty(capsys):
    """Empty project_path must not invoke fixer."""
    import orchestrator

    fixer = MagicMock(return_value=True)
    result = orchestrator._verify_symlinks_consistent("", fixer)
    assert result is False
    fixer.assert_not_called()
    captured = capsys.readouterr()
    assert "[WARN]" in captured.out or "[WARN]" in captured.err


def test_verify_returns_false_when_project_path_whitespace_only(capsys):
    """Whitespace-only project_path must not invoke fixer."""
    import orchestrator

    fixer = MagicMock(return_value=True)
    result = orchestrator._verify_symlinks_consistent("   ", fixer)
    assert result is False
    fixer.assert_not_called()
    captured = capsys.readouterr()
    assert "[WARN]" in captured.out or "[WARN]" in captured.err
