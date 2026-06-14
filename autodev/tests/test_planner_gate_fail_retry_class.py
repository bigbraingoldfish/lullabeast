"""P1-B — planner ``gate_fail`` carries ``retry_class`` (consistency fix).

The executor ``gate_fail`` always carries ``retry_class`` and the reviewer carries it
conditionally, but the planner ``gate_fail`` omitted it — so the activity feed could
not label a planner failure's retry source uniformly with the other two agents.

This is a source drift-guard (the emit is inline in the main loop and not unit-drivable
without a full planner-failure integration harness): it asserts every ``gate_fail`` emit
in the orchestrator — planner, executor, reviewer — carries a ``retry_class`` field. It
fails today on the planner emit and stays green once the field is added; if a future
edit drops ``retry_class`` from any of the three, this catches it.
"""
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _gate_fail_blocks(src):
    """Return the full text of each ``_write_pipeline_event(...)`` call whose args
    include ``"gate_fail"``. Extracted by balancing parentheses from the opening
    ``(`` (regex can't balance the nested parens in the detail dict). The emits
    contain no string literals with unbalanced parens, so this is exact."""
    blocks = []
    for m in re.finditer(r"_write_pipeline_event\(", src):
        depth, i = 0, m.end() - 1  # start at the '('
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        call = src[m.start():i + 1]
        if '"gate_fail"' in call:
            blocks.append(call)
    return blocks


def _block_agent(block):
    """Extract the agent (3rd positional arg) of a gate_fail emit window."""
    m = re.search(r'"gate_fail"\s*,\s*[A-Za-z_]+\s*,\s*"(\w+)"', block)
    return m.group(1) if m else None


def test_all_gate_fail_emits_carry_retry_class():
    src = open(orch_mod.__file__, "r", encoding="utf-8").read()
    blocks = _gate_fail_blocks(src)
    # planner, executor, reviewer
    assert len(blocks) >= 3, f"expected >=3 gate_fail emits, found {len(blocks)}"
    missing = [_block_agent(b) for b in blocks if "retry_class" not in b]
    assert not missing, (
        "every gate_fail emit must carry retry_class for uniform retry-source "
        f"labeling; missing on agent(s): {missing}"
    )


def test_planner_gate_fail_block_carries_retry_class():
    """Targeted: the planner ``gate_fail`` (agent == 'planner') carries retry_class."""
    src = open(orch_mod.__file__, "r", encoding="utf-8").read()
    planner_blocks = [b for b in _gate_fail_blocks(src) if _block_agent(b) == "planner"]
    assert planner_blocks, "could not locate the planner gate_fail emit"
    assert all("retry_class" in b for b in planner_blocks)
