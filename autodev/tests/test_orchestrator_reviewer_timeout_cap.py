"""ISSUE D+E: Reviewer sentinel-timeout must cap at 3 and escalate, and must
write failure_context on every timeout (not only when the gate runs).

Finding D: the `if not sentinel_found:` block in the reviewer branch
only calls increment_reviewer_retries() and continues — no cap check.
The planner caps at retries >= 3; the reviewer must match.

Finding E: write_failure_context("reviewer", ...) is called only when
the gate evaluates real output (sentinel found + gate non-PASS). It is
NOT called on sentinel timeout, leaving stale executor failure context
visible to operators and the escalation agent.

Both fixes are in the same four-line block (orchestrator.py lines ~2521-2524).
These AST-static tests will FAIL until both fixes are implemented.

Fix-IDs: reviewer-timeout-cap (D), reviewer-timeout-failure-context (E)
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


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _parse_orchestrator():
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    return ast.parse(source), source


def _collect_sentinel_timeout_block(tree):
    """Return AST nodes inside `if not sentinel_found:` within the reviewer branch.

    Walks the reviewer elif block (current_agent == "reviewer"), finds the
    `if not sentinel_found:` node, and returns a flat list of all Call nodes
    inside that sub-tree.
    """
    calls_in_timeout_block = []
    has_cap_check = []        # True if a numeric comparison follows increment
    sets_escalation = []      # True if current_agent is set to "escalation"

    class ReviewerVisitor(ast.NodeVisitor):
        def __init__(self):
            self._in_reviewer = False

        def visit_If(self, node):
            # Detect the reviewer elif
            is_reviewer = False
            test = node.test
            if isinstance(test, ast.Compare):
                left, comps, ops = test.left, test.comparators, test.ops
                if isinstance(ops[0], ast.Eq) and (
                    (isinstance(left, ast.Constant) and left.value == "reviewer")
                    or (len(comps) == 1
                        and isinstance(comps[0], ast.Constant)
                        and comps[0].value == "reviewer")
                ):
                    is_reviewer = True

            if is_reviewer:
                prev = self._in_reviewer
                self._in_reviewer = True
                self._scan_for_timeout_block(node)
                self._in_reviewer = prev
            else:
                self.generic_visit(node)

        def _scan_for_timeout_block(self, reviewer_node):
            """Inside the reviewer block, find `if not sentinel_found:` and inspect it."""
            for child in ast.walk(reviewer_node):
                if not isinstance(child, ast.If):
                    continue
                test = child.test
                # Match:  if not sentinel_found:
                if (
                    isinstance(test, ast.UnaryOp)
                    and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Name)
                    and test.operand.id == "sentinel_found"
                ):
                    # Collect all calls inside this block
                    for n in ast.walk(child):
                        if isinstance(n, ast.Call):
                            func = n.func
                            name = (
                                func.id if isinstance(func, ast.Name)
                                else (func.attr if isinstance(func, ast.Attribute) else "")
                            )
                            calls_in_timeout_block.append((name, getattr(n, "lineno", -1)))

                    # Check for a cap comparison (Compare with a numeric literal)
                    for n in ast.walk(child):
                        if isinstance(n, ast.Compare):
                            for comp in n.comparators:
                                if isinstance(comp, ast.Constant) and isinstance(comp.value, int):
                                    has_cap_check.append(comp.value)

                    # Check that current_agent is assigned "escalation" somewhere inside
                    for n in ast.walk(child):
                        if (
                            isinstance(n, ast.Assign)
                            and len(n.targets) == 1
                            and isinstance(n.targets[0], ast.Subscript)
                        ):
                            val = n.value
                            if isinstance(val, ast.Constant) and val.value == "escalation":
                                sets_escalation.append(getattr(n, "lineno", -1))

    ReviewerVisitor().visit(tree)
    return calls_in_timeout_block, has_cap_check, sets_escalation


# ---------------------------------------------------------------------------
# Finding E: write_failure_context called on sentinel timeout
# ---------------------------------------------------------------------------


def test_reviewer_sentinel_timeout_calls_write_failure_context():
    """Finding E: write_failure_context must be called in the sentinel-timeout block.

    Currently the block is:
        if not sentinel_found:
            print("[ERROR] Sentinel timeout")
            self.increment_reviewer_retries()
            continue

    write_failure_context is NOT present — stale failure_context.json misleads
    operators during reviewer timeout sequences.
    """
    tree, _ = _parse_orchestrator()
    calls, _, _ = _collect_sentinel_timeout_block(tree)
    call_names = [name for name, _ in calls]
    assert "write_failure_context" in call_names, (
        "write_failure_context must be called inside `if not sentinel_found:` in the "
        "reviewer branch so operators see an up-to-date failure record on each timeout. "
        f"Calls found in that block: {call_names}. "
        "Finding E (reviewer-timeout-failure-context) not yet implemented."
    )


# ---------------------------------------------------------------------------
# Finding D: cap check and escalation in the sentinel-timeout block
# ---------------------------------------------------------------------------


def test_reviewer_sentinel_timeout_has_numeric_cap():
    """Finding D: a numeric cap must appear inside the sentinel-timeout block.

    After increment_reviewer_retries(), the block must compare the returned
    value against a numeric literal (the cap) to decide whether to escalate.
    Owner confirmed cap = 3.
    """
    tree, _ = _parse_orchestrator()
    _, cap_values, _ = _collect_sentinel_timeout_block(tree)
    assert cap_values, (
        "No numeric comparison found inside `if not sentinel_found:` in the reviewer "
        "branch. A cap check (e.g. `if _rv_retries >= 3:`) must be present so the "
        "reviewer does not loop indefinitely on sentinel timeouts. "
        "Finding D (reviewer-timeout-cap) not yet implemented."
    )
    assert any(v >= 1 for v in cap_values), (
        f"Cap value must be >= 1 (owner confirmed 3). Found: {cap_values}"
    )


def test_reviewer_sentinel_timeout_sets_escalation_on_cap():
    """Finding D: on hitting the cap, current_agent must be set to 'escalation'.

    The transition from reviewer to escalation on timeout must mirror the planner's
    `if retries >= 3: self.state['current_agent'] = 'escalation'` pattern.
    """
    tree, _ = _parse_orchestrator()
    _, _, esc_lines = _collect_sentinel_timeout_block(tree)
    assert esc_lines, (
        "No assignment `self.state[...] = 'escalation'` found inside `if not "
        "sentinel_found:` in the reviewer branch. On hitting the retry cap, the "
        "orchestrator must transition to the escalation agent. "
        "Finding D (reviewer-timeout-cap) not yet implemented."
    )


def test_reviewer_sentinel_timeout_calls_transition_state():
    """Finding D: transition_state must be called after the cap sets escalation."""
    tree, _ = _parse_orchestrator()
    calls, _, _ = _collect_sentinel_timeout_block(tree)
    call_names = [name for name, _ in calls]
    assert "transition_state" in call_names, (
        "transition_state must be called inside `if not sentinel_found:` in the "
        "reviewer branch after the cap check sets current_agent = 'escalation'. "
        f"Calls found in that block: {call_names}. "
        "Finding D (reviewer-timeout-cap) not yet implemented."
    )


# ---------------------------------------------------------------------------
# Order: write_failure_context before cap check
# ---------------------------------------------------------------------------


def test_reviewer_timeout_failure_context_called_before_cap_check():
    """write_failure_context must appear before the cap / transition_state calls.

    The plan requires: write context first (so even the final attempt is recorded),
    then check cap and escalate.  This test verifies line-number ordering.
    """
    tree, _ = _parse_orchestrator()
    calls, _, _ = _collect_sentinel_timeout_block(tree)
    wfc_lines = [ln for name, ln in calls if name == "write_failure_context"]
    ts_lines = [ln for name, ln in calls if name == "transition_state"]
    if not wfc_lines or not ts_lines:
        pytest.skip("write_failure_context or transition_state not yet present — "
                    "run after both D and E fixes land")
    assert min(wfc_lines) < min(ts_lines), (
        f"write_failure_context (line {min(wfc_lines)}) must appear before "
        f"transition_state (line {min(ts_lines)}) inside the sentinel-timeout block."
    )
