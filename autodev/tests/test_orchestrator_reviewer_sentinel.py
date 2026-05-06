"""Reviewer branch must use poll_for_sentinel (not poll_for_sentinel_with_idle_detect).

With the autodev-pipeline-signals plugin installed, ``agent_end`` writes the
sentinel synchronously when the session closes.  ``poll_for_sentinel`` is now
the correct function for all three pipeline agents — idle detection is no
longer needed as the authoritative signal comes from the plugin hook.

References the implementation-complete state after the agent_end integration.

FIND-ID: F4 (updated — formerly enforced idle-detect requirement, now enforces
the opposite: plain poll_for_sentinel with min_sentinel_mtime is correct)
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
        "plain_calls": [(lineno, ...),],       # poll_for_sentinel calls
        "idle_detect_calls": [(lineno, ...),], # poll_for_sentinel_with_idle_detect calls
      }
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    plain_calls = []
    idle_detect_calls = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self._in_reviewer = False

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
                        or (
                            len(comps) == 1
                            and isinstance(comps[0], ast.Constant)
                            and comps[0].value == "reviewer"
                        )
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


def test_reviewer_branch_calls_plain_poll_for_sentinel():
    """post-agent_end: reviewer branch must use poll_for_sentinel.

    The autodev-pipeline-signals plugin delivers the completion signal via
    agent_end, so idle detection is unnecessary.  poll_for_sentinel with
    min_sentinel_mtime is the correct call in the reviewer branch.
    """
    result = _find_reviewer_sentinel_calls()
    assert result["plain_calls"], (
        "poll_for_sentinel is NOT called in the reviewer branch. "
        "After the agent_end plugin integration, the reviewer branch must use "
        "poll_for_sentinel (not poll_for_sentinel_with_idle_detect)."
    )


def test_reviewer_branch_does_not_call_idle_detect_poll():
    """post-agent_end: poll_for_sentinel_with_idle_detect must NOT appear in reviewer branch.

    Idle detection was replaced by the agent_end plugin hook.  The idle-detect
    variant has been removed from sentinel_poller.py; any reference here would
    be a regression.
    """
    result = _find_reviewer_sentinel_calls()
    assert result["idle_detect_calls"] == [], (
        f"poll_for_sentinel_with_idle_detect is called in the reviewer branch "
        f"at lines {result['idle_detect_calls']}. "
        "This function has been removed — replace with poll_for_sentinel "
        "(the agent_end plugin now writes the .done sentinel authoritatively)."
    )


def test_reviewer_sentinel_passes_min_sentinel_mtime():
    """The poll_for_sentinel call in the reviewer branch must pass min_sentinel_mtime.

    This stale-sentinel guard was moved from poll_for_sentinel_with_idle_detect
    into poll_for_sentinel.  It must be passed so orphaned sentinels from a
    prior session reset are discarded.
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    calls_with_mtime = []
    calls_without_mtime = []

    class MtimeVisitor(ast.NodeVisitor):
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
                    or (
                        len(comps) == 1
                        and isinstance(comps[0], ast.Constant)
                        and comps[0].value == "reviewer"
                    )
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
            if name != "poll_for_sentinel":
                return
            kw_names = {kw.arg for kw in node.keywords}
            if "min_sentinel_mtime" in kw_names:
                calls_with_mtime.append(node.lineno)
            else:
                calls_without_mtime.append(node.lineno)

    MtimeVisitor().visit(tree)

    assert calls_with_mtime, (
        "poll_for_sentinel in the reviewer branch must include "
        "min_sentinel_mtime=_attempt_start_time to discard orphaned prior-session sentinels."
    )
    assert not calls_without_mtime, (
        f"poll_for_sentinel at lines {calls_without_mtime} in the reviewer branch "
        "is missing the min_sentinel_mtime= keyword argument."
    )


def test_reviewer_branch_still_reads_sessions_json():
    """The reviewer branch still reads sessions.json for token accounting.

    Even though the 15-retry JSONL loop is gone, a single post-sentinel read of
    sessions.json must remain for token accumulation.
    """
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    sessions_json_referenced = []

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
                    or (
                        len(comps) == 1
                        and isinstance(comps[0], ast.Constant)
                        and comps[0].value == "reviewer"
                    )
                ):
                    is_reviewer = True

            if is_reviewer:
                prev = self._in_reviewer
                self._in_reviewer = True
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value == "sessions.json":
                        sessions_json_referenced.append(getattr(child, "lineno", -1))
                self._in_reviewer = prev
            else:
                self.generic_visit(node)

    V().visit(tree)

    assert sessions_json_referenced, (
        "The reviewer branch must still read sessions.json for token accounting. "
        "No reference to 'sessions.json' was found in the reviewer branch."
    )
