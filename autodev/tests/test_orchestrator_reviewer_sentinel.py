"""F4: Reviewer branch must use poll_for_sentinel_with_idle_detect, not poll_for_sentinel.

The simpler poll_for_sentinel has no idle detection. When a reviewer session is active
but writing no JSONL output, poll_for_sentinel will declare timeout at 600s regardless
of actual activity. poll_for_sentinel_with_idle_detect resets its idle clock on any
file write in watch_dirs, catching the common case of an active-but-silent session.

FIND-ID: F4
"""

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _find_reviewer_sentinel_calls():
    """Parse orchestrator.py AST and find all poll_for_sentinel* Call nodes inside
    the reviewer branch (the elif current_agent == 'reviewer': block).

    Returns a dict:
      {
        "plain_calls": [(lineno, ...),],      # poll_for_sentinel calls
        "idle_detect_calls": [(lineno, ...),], # poll_for_sentinel_with_idle_detect calls
      }
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    # Walk looking for the reviewer elif block
    plain_calls = []
    idle_detect_calls = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self._in_reviewer = False
            self._reviewer_depth = 0

        def visit_Call(self, node):
            if self._in_reviewer:
                func = node.func
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                else:
                    name = ""
                if name == "poll_for_sentinel_with_idle_detect":
                    idle_detect_calls.append(node.lineno)
                elif name == "poll_for_sentinel":
                    plain_calls.append(node.lineno)
            self.generic_visit(node)

        def visit_If(self, node):
            # Look for:  current_agent == "reviewer"  or  "reviewer" == current_agent
            is_reviewer = False
            test = node.test
            if isinstance(test, ast.Compare):
                left = test.left
                comps = test.comparators
                ops = test.ops
                if (
                    isinstance(ops[0], ast.Eq)
                    and (
                        (isinstance(left, ast.Constant) and left.value == "reviewer")
                        or (len(comps) == 1 and isinstance(comps[0], ast.Constant) and comps[0].value == "reviewer")
                    )
                ):
                    is_reviewer = True

            if is_reviewer:
                prev = self._in_reviewer
                self._in_reviewer = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        self.visit_Call(child)
                self._in_reviewer = prev
            else:
                self.generic_visit(node)

    Visitor().visit(tree)
    return {"plain_calls": plain_calls, "idle_detect_calls": idle_detect_calls}


def test_reviewer_branch_does_not_call_plain_poll_for_sentinel():
    """F4: poll_for_sentinel (plain) must NOT appear in the reviewer elif block.

    The plain poller has no idle detection. Using it for the reviewer means any
    reviewer session that writes no JSONL output will be declared timed-out after
    600s even if the session is actively writing project files.
    """
    result = _find_reviewer_sentinel_calls()
    assert result["plain_calls"] == [], (
        f"poll_for_sentinel (plain, no idle detection) is called in the reviewer branch "
        f"at lines {result['plain_calls']}. Replace with poll_for_sentinel_with_idle_detect "
        f"to prevent premature idle timeouts."
    )


def test_reviewer_branch_calls_idle_detect_poll():
    """F4: poll_for_sentinel_with_idle_detect must appear in the reviewer elif block.

    The reviewer may write project files without writing JSONL output (especially
    for lightweight reviews). Idle detection via watch_dirs prevents false timeouts.
    """
    result = _find_reviewer_sentinel_calls()
    assert result["idle_detect_calls"], (
        "poll_for_sentinel_with_idle_detect is NOT called in the reviewer branch. "
        "The reviewer must use the idle-detect variant (with watch_dirs=[SYMLINK_TARGET] "
        "and min_sentinel_mtime=_attempt_start_time) to match the executor pattern."
    )


def test_reviewer_sentinel_passes_watch_dirs():
    """F4: The idle-detect call in the reviewer branch must pass watch_dirs.

    Without watch_dirs the poller can only observe JSONL writes, not file-system
    activity in the project directory — causing false idle timeouts.
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    calls_with_watch_dirs = []
    calls_without_watch_dirs = []

    class WatchDirsVisitor(ast.NodeVisitor):
        def __init__(self):
            self._in_reviewer = False

        def visit_If(self, node):
            test = node.test
            is_reviewer = False
            if isinstance(test, ast.Compare):
                left = test.left
                comps = test.comparators
                ops = test.ops
                if isinstance(ops[0], ast.Eq) and (
                    (isinstance(left, ast.Constant) and left.value == "reviewer")
                    or (len(comps) == 1 and isinstance(comps[0], ast.Constant) and comps[0].value == "reviewer")
                ):
                    is_reviewer = True

            if is_reviewer:
                prev = self._in_reviewer
                self._in_reviewer = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        self._check_call(child)
                self._in_reviewer = prev
            else:
                self.generic_visit(node)

        def _check_call(self, node):
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                return
            if name != "poll_for_sentinel_with_idle_detect":
                return
            kw_names = {kw.arg for kw in node.keywords}
            if "watch_dirs" in kw_names:
                calls_with_watch_dirs.append(node.lineno)
            else:
                calls_without_watch_dirs.append(node.lineno)

    WatchDirsVisitor().visit(tree)

    assert calls_with_watch_dirs, (
        "poll_for_sentinel_with_idle_detect in the reviewer branch must include "
        "watch_dirs=[SYMLINK_TARGET] so file-system activity resets the idle clock."
    )
    assert not calls_without_watch_dirs, (
        f"poll_for_sentinel_with_idle_detect at lines {calls_without_watch_dirs} "
        "in the reviewer branch is missing watch_dirs= keyword argument."
    )


def test_reviewer_branch_resolves_jsonl_path():
    """The reviewer branch must look up the active session's JSONL file from
    sessions.json — same pattern as the executor branch — so that
    poll_for_sentinel_with_idle_detect receives a real jsonl_path (not None)
    and can use JSONL mtime as a secondary idle signal alongside watch_dirs.

    Static check: an open of "sessions.json" appears inside the reviewer branch
    AND the poll_for_sentinel_with_idle_detect call's first positional /
    second positional argument is NOT a Constant(None).
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    sessions_json_referenced = []
    poll_calls_with_none_jsonl = []
    poll_calls_with_real_jsonl = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self._in_reviewer = False

        def visit_If(self, node):
            test = node.test
            is_reviewer = False
            if isinstance(test, ast.Compare):
                left = test.left
                comps = test.comparators
                ops = test.ops
                if isinstance(ops[0], ast.Eq) and (
                    (isinstance(left, ast.Constant) and left.value == "reviewer")
                    or (len(comps) == 1 and isinstance(comps[0], ast.Constant) and comps[0].value == "reviewer")
                ):
                    is_reviewer = True

            if is_reviewer:
                prev = self._in_reviewer
                self._in_reviewer = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value == "sessions.json":
                        sessions_json_referenced.append(getattr(child, "lineno", -1))
                    if isinstance(child, ast.Call):
                        f = child.func
                        name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                        if name == "poll_for_sentinel_with_idle_detect":
                            # Second positional arg is jsonl_path
                            args = child.args
                            if len(args) >= 2:
                                a = args[1]
                                if isinstance(a, ast.Constant) and a.value is None:
                                    poll_calls_with_none_jsonl.append(child.lineno)
                                else:
                                    poll_calls_with_real_jsonl.append(child.lineno)
                            else:
                                # 1-arg form — jsonl_path must be a kwarg or missing
                                kwargs = {kw.arg: kw.value for kw in child.keywords}
                                if "jsonl_path" in kwargs:
                                    a = kwargs["jsonl_path"]
                                    if isinstance(a, ast.Constant) and a.value is None:
                                        poll_calls_with_none_jsonl.append(child.lineno)
                                    else:
                                        poll_calls_with_real_jsonl.append(child.lineno)
                self._in_reviewer = prev
            else:
                self.generic_visit(node)

    V().visit(tree)

    assert sessions_json_referenced, (
        "The reviewer branch must read the OpenClaw sessions.json to resolve "
        "the active session's JSONL path. No reference to 'sessions.json' was "
        "found in the reviewer branch."
    )
    assert not poll_calls_with_none_jsonl, (
        f"poll_for_sentinel_with_idle_detect calls at lines {poll_calls_with_none_jsonl} "
        "pass None as jsonl_path. Resolve the session JSONL via sessions.json "
        "(mirroring the executor pattern) and pass it as the second argument."
    )
    assert poll_calls_with_real_jsonl, (
        "poll_for_sentinel_with_idle_detect in the reviewer branch must receive "
        "a resolved JSONL path variable as its second argument."
    )
